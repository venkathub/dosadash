"""Forecasting tests (skip cleanly when the [forecast]/[train] extras are absent)."""

from datetime import date, timedelta

import pytest

pd = pytest.importorskip("pandas")
xgb = pytest.importorskip("xgboost")

from dosadash_ml.forecasting.dataset import synthetic_daily_sales  # noqa: E402
from dosadash_ml.forecasting.features import (  # noqa: E402
    FEATURES,
    ItemMeta,
    add_features,
    feature_row,
    make_dense_daily,
)
from dosadash_ml.forecasting.predict import forecast_next_days, load_champion  # noqa: E402

META = ItemMeta(item_id=1, category="Dosa", is_veg=True)


def _dense_single_item(qtys: list[float], start: date) -> pd.DataFrame:
    sales = pd.DataFrame(
        {
            "item_id": [1] * len(qtys),
            "date": [start + timedelta(days=i) for i in range(len(qtys))],
            "qty": qtys,
        }
    )
    return make_dense_daily(sales, [META], start, start + timedelta(days=len(qtys) - 1))


def test_dense_grid_zero_fills_missing_days():
    start = date(2026, 3, 2)
    sales = pd.DataFrame({"item_id": [1], "date": [start], "qty": [5.0]})
    dense = make_dense_daily(sales, [META], start, start + timedelta(days=3))
    assert len(dense) == 4
    assert dense["qty"].tolist() == [5.0, 0.0, 0.0, 0.0]


def test_lag_features_correct():
    start = date(2026, 3, 2)  # Monday
    qtys = [float(i) for i in range(30)]
    feats = add_features(_dense_single_item(qtys, start))
    first = feats.iloc[0]  # first row surviving the 14-day warm-up
    assert first["lag_14"] == first["qty"] - 14
    assert first["lag_7"] == first["qty"] - 7
    assert first["ma_7"] == pytest.approx(first["qty"] - 4)  # mean of previous 7
    assert set(FEATURES) <= set(feats.columns)


def test_feature_row_mirrors_add_features():
    """Training path (pandas) and scoring path (feature_row) must agree —
    any skew silently damages the model."""
    start = date(2026, 3, 2)
    qtys = [3.0, 8.0, 1.0, 4.0, 9.0, 2.0, 7.0] * 4 + [5.0, 6.0]
    feats = add_features(_dense_single_item(qtys, start))
    last = feats.iloc[-1]
    day = last["date"] if isinstance(last["date"], date) else last["date"].date()
    scored = feature_row(qtys[:-1], META, day)
    trained = [float(last[f]) for f in FEATURES]
    assert scored == pytest.approx(trained)


def test_feature_row_pads_short_history():
    row = feature_row([2.0], META, date(2026, 1, 15))  # Pongal
    assert row[0] == 0.0 and row[1] == 0.0  # lags padded with zeros
    assert row[5] == 1.0  # Plain-Dosa category: no Pongal multiplier


def test_train_register_export_score_roundtrip(tmp_path):
    mlflow = pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    from dosadash_ml.forecasting.train import (
        CHAMPION,
        MODEL_NAME,
        export_champion,
        register_and_maybe_promote,
        train,
    )

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path}/mlflow.db")
    mlflow.set_experiment("demand-forecast-test")

    sales, metas, start, end = synthetic_daily_sales(users=30, days=90, seed=7)
    frame = add_features(make_dense_daily(sales, metas, start, end))
    model, metrics = train(frame, valid_days=14, n_estimators=20)
    assert 0.0 < metrics["valid_wape"] < 2.0

    version, promoted = register_and_maybe_promote(model, metrics, params={"test": True})
    assert promoted  # first version always takes the champion alias
    champ = MlflowClient().get_model_version_by_alias(MODEL_NAME, CHAMPION)
    assert str(champ.version) == version

    export_champion(model, metrics, version, tmp_path / "artifacts")
    loaded = load_champion(tmp_path / "artifacts")
    assert loaded.version == f"{MODEL_NAME}/v{version}"

    history = {m.item_id: [4.0] * 30 for m in metas}
    meta_map = {m.item_id: m for m in metas}
    rows = forecast_next_days(loaded, history, meta_map, start=end + timedelta(days=1), horizon=14)
    assert len(rows) == len(metas) * 14
    assert all(r.predicted_qty >= 0.0 for r in rows)


def test_committed_champion_artifacts_load():
    """The repo ships scoreable champion artifacts (baked into the worker image)."""
    from pathlib import Path

    artifacts = Path(__file__).parents[1] / "artifacts"
    loaded = load_champion(artifacts)
    assert loaded.meta["features"] == FEATURES
    assert loaded.meta["valid_wape"] < loaded.meta["baseline_lag7_wape"]  # beats naive lag-7
