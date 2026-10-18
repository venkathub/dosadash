"""Forecast policy, time leakage, integrity and rollback drills."""

import json
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from dosadash_ml.forecasting.monitoring import degradation, wape


def test_monitor_requires_complete_comparable_evidence():
    args = ([10.0] * 14, [11.0] * 14, [10.0] * 14, [16.0] * 14)
    assert degradation(*args, complete_days=28, model_versions=["v1"])["status"] == "degraded"
    assert (
        degradation(*args, complete_days=27, model_versions=["v1"])["status"] == "insufficient_data"
    )
    assert (
        degradation(*args, complete_days=28, model_versions=["v1", "v2"])["status"]
        == "insufficient_data"
    )
    assert wape([0], [20]) is None
    with pytest.raises(ValueError):
        wape([10], [float("nan")])


def test_promotion_rejects_regression_nan_zero_and_baseline_loss():
    pytest.importorskip("xgboost")
    from dosadash_ml.forecasting.lifecycle import promotion_decision

    folds = [
        {"candidate_wape": 0.15, "incumbent_wape": 0.3, "baseline_wape": 0.2, "actual_sum": 100}
        for _ in range(3)
    ]
    report = {"validation": folds}
    assert promotion_decision(report)
    folds[0]["candidate_wape"] = 0.32
    assert not promotion_decision(report)  # any fold >5% regression rejects
    for value in [float("nan"), float("inf"), None]:
        folds[0]["candidate_wape"] = value
        assert not promotion_decision(report)
    folds[0]["candidate_wape"] = 0.15
    folds[0]["actual_sum"] = 0
    assert not promotion_decision(report)
    assert not promotion_decision({"validation": folds[:2]})


def test_recursive_window_does_not_see_future_actuals():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("xgboost")
    from dosadash_ml.forecasting.features import ItemMeta, make_dense_daily
    from dosadash_ml.forecasting.lifecycle import fit, score_window

    start = date(2026, 1, 1)
    meta = [ItemMeta(item_id=1, category="Dosa", is_veg=True)]
    sales = pd.DataFrame(
        [{"item_id": 1, "date": start + timedelta(days=i), "qty": 10 + i % 7} for i in range(70)]
    )
    dense = make_dense_daily(sales, meta, start, start + timedelta(days=69))
    origin = start + timedelta(days=56)
    model = fit(dense[dense.date < origin], trees=2)
    original_predict = model.booster.predict
    captured = []

    def capture(matrix):
        result = original_predict(matrix)
        captured.append(result.tolist())
        return result

    model.booster.predict = capture
    score_window(model, dense, meta, origin, 14)
    first = list(captured)
    captured.clear()
    dense.loc[dense.date >= origin, "qty"] = 9999
    score_window(model, dense, meta, origin, 14)
    assert captured == first


def test_release_rollback_restores_real_model_and_blocks_tampering(tmp_path):
    pytest.importorskip("xgboost")
    from pathlib import Path

    from dosadash_ml.forecasting.lifecycle import (
        atomic_json,
        release,
        rollback,
        store_lock,
        verify_release,
    )
    from dosadash_ml.forecasting.predict import load_champion

    champion = load_champion(Path(__file__).parents[1] / "artifacts")
    with store_lock(tmp_path):
        old = release(tmp_path, champion, {"evidence": "rollback_test"})
        new = release(tmp_path, champion, {"evidence": "rollback_test"})
        atomic_json(tmp_path / "active.json", {"active": new, "previous": old, "history": []})
    state = rollback(tmp_path, "Deliberate rollback drill, no production impact")
    assert state["active"] == old and state["previous"] is None
    verify_release(tmp_path, old)
    with pytest.raises(ValueError, match="no previous"):
        rollback(tmp_path, "Repeated rollback must not oscillate")
    meta = tmp_path / "releases" / old / "forecast/champion/meta.json"
    meta.write_text(meta.read_text() + " ")
    with pytest.raises(ValueError, match="integrity"):
        verify_release(tmp_path, old)
    assert json.loads((tmp_path / "active.json").read_text())["active"] == old


def test_registry_outage_never_means_empty_champion():
    pytest.importorskip("mlflow")
    from dosadash_ml.forecasting.train import _current_champion_tags

    client = Mock()
    client.get_registered_model.side_effect = RuntimeError("outage")
    with pytest.raises(RuntimeError):
        _current_champion_tags(client)
