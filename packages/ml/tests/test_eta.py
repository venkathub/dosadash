"""ETA regression tests (skip cleanly without the [infer]/[train] extras)."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dosadash_ml.eta.features import ETA_FEATURES, eta_feature_row, heuristic_eta_minutes

MONDAY_NOON = datetime(2026, 6, 1, 12, 30)  # peak lunch
MONDAY_1600 = datetime(2026, 6, 1, 16, 0)  # off-peak
SAT_1600 = datetime(2026, 6, 6, 16, 0)


def test_feature_row_shape_and_values():
    row = eta_feature_row(max_prep=20, total_qty=3, n_lines=2, when=SAT_1600)
    assert len(row) == len(ETA_FEATURES)
    assert row == [20.0, 3.0, 2.0, 16.0, 5.0, 1.0, 0.0]


def test_heuristic_bounded_with_peak_and_weekend_signal():
    base = heuristic_eta_minutes(max_prep=15, total_qty=2, when=MONDAY_1600)
    peak = heuristic_eta_minutes(max_prep=15, total_qty=2, when=MONDAY_NOON)
    weekend = heuristic_eta_minutes(max_prep=15, total_qty=2, when=SAT_1600)
    assert 18 <= base <= 90
    assert peak == base + 8
    assert weekend == base + 5


def test_festival_bump():
    pongal = heuristic_eta_minutes(max_prep=15, total_qty=2, when=datetime(2026, 1, 15, 16, 0))
    normal = heuristic_eta_minutes(max_prep=15, total_qty=2, when=datetime(2026, 1, 8, 16, 0))
    assert pongal == normal + 6


def test_train_export_score_roundtrip(tmp_path):
    pytest.importorskip("xgboost")
    mlflow = pytest.importorskip("mlflow")

    from dosadash_ml.eta.predict import load_eta_champion, predict_eta_minutes
    from dosadash_ml.eta.train import (
        export_champion,
        register_and_maybe_promote,
        synthetic_eta_dataset,
        train_eta,
    )

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    mlflow.set_experiment("eta-test")

    x, y, heuristic, dates = synthetic_eta_dataset(users=40, days=90, seed=7)
    model, metrics = train_eta(x, y, heuristic, dates, valid_days=14, n_estimators=30)
    version, promoted = register_and_maybe_promote(model, metrics, params={"test": True})
    assert promoted
    export_champion(model, metrics, version, tmp_path / "artifacts")

    champ = load_eta_champion(tmp_path / "artifacts")
    minutes = predict_eta_minutes(champ, max_prep=20, total_qty=3, n_lines=2, when=MONDAY_NOON)
    assert 15 <= minutes <= 120


def test_committed_eta_artifacts_load_and_score_sanely():
    pytest.importorskip("xgboost")
    from dosadash_ml.eta.predict import load_eta_champion, predict_eta_minutes

    champ = load_eta_champion(Path(__file__).parents[1] / "artifacts")
    assert champ.meta["features"] == ETA_FEATURES
    # Synthetic noise floor is ~3.3 min MAE; champion must sit at it, not above.
    assert champ.meta["valid_mae"] <= champ.meta["baseline_heuristic_mae"] + 0.5

    quick = predict_eta_minutes(champ, max_prep=12, total_qty=1, n_lines=1, when=MONDAY_1600)
    big_peak = predict_eta_minutes(
        champ, max_prep=25, total_qty=8, n_lines=4, when=MONDAY_NOON + timedelta(days=5)
    )
    assert quick < big_peak  # bigger peak-hour weekend order → longer ETA
    assert 15 <= quick <= 120 and 15 <= big_peak <= 120
