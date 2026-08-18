"""Train the demand forecaster + manage the MLflow registry (local/CI only).

    uv run python -m dosadash_ml.forecasting.train --synthetic \
        [--tracking-uri sqlite:///packages/ml/mlflow.db] \
        [--export-dir packages/ml/artifacts]

Promotion policy: a new version takes the `champion` alias iff there is no
champion yet, or its validation WAPE improves on the current champion's
(tag `wape` on the model version). The champion booster + meta are exported
to `--export-dir` and baked into the worker image — MLflow itself never runs
on the VPS (docs/02).
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import xgboost as xgb
from mlflow.tracking import MlflowClient

from dosadash_ml.forecasting.dataset import synthetic_daily_sales
from dosadash_ml.forecasting.features import FEATURES, TARGET, add_features, make_dense_daily

MODEL_NAME = "dosadash-demand-forecast"
CHAMPION = "champion"


def wape(actual: np.ndarray, pred: np.ndarray) -> float:
    denom = float(np.abs(actual).sum())
    return float(np.abs(actual - pred).sum()) / denom if denom else 0.0


def train(
    frame: pd.DataFrame,
    *,
    valid_days: int = 28,
    n_estimators: int = 300,
) -> tuple[xgb.XGBRegressor, dict[str, float]]:
    """Time-split train/valid; returns model + metrics incl. naive baseline."""
    cutoff = pd.to_datetime(frame["date"]).max() - pd.Timedelta(days=valid_days)
    is_valid = pd.to_datetime(frame["date"]) > cutoff
    train_df, valid_df = frame[~is_valid], frame[is_valid]

    model = xgb.XGBRegressor(
        n_estimators=n_estimators,
        max_depth=6,
        learning_rate=0.06,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        tree_method="hist",
        random_state=42,
    )
    model.fit(train_df[FEATURES], train_df[TARGET])

    actual = valid_df[TARGET].to_numpy()
    pred = np.clip(model.predict(valid_df[FEATURES]), 0.0, None)
    naive = valid_df["lag_7"].to_numpy()  # same-weekday-last-week baseline
    metrics = {
        "valid_wape": wape(actual, pred),
        "valid_mae": float(np.abs(actual - pred).mean()),
        "baseline_lag7_wape": wape(actual, naive),
        "baseline_lag7_mae": float(np.abs(actual - naive).mean()),
        "train_rows": float(len(train_df)),
        "valid_rows": float(len(valid_df)),
    }
    return model, metrics


def _current_champion_wape(client: MlflowClient) -> float | None:
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except Exception:
        return None
    tag = version.tags.get("wape")
    return float(tag) if tag is not None else None


def register_and_maybe_promote(
    model: xgb.XGBRegressor, metrics: dict[str, float], *, params: dict[str, object]
) -> tuple[str, bool]:
    """Log run, register version, promote to champion if WAPE improves."""
    client = MlflowClient()
    with mlflow.start_run(run_name=f"demand-forecast-{datetime.now(UTC):%Y%m%d-%H%M}"):
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        info = mlflow.xgboost.log_model(model, name="model", registered_model_name=MODEL_NAME)
    version = str(info.registered_model_version)
    client.set_model_version_tag(MODEL_NAME, version, "wape", f"{metrics['valid_wape']:.6f}")

    incumbent = _current_champion_wape(client)
    promote = incumbent is None or metrics["valid_wape"] <= incumbent
    if promote:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    return version, promote


def export_champion(
    model: xgb.XGBRegressor, metrics: dict[str, float], version: str, export_dir: Path
) -> Path:
    champ_dir = export_dir / "forecast" / "champion"
    champ_dir.mkdir(parents=True, exist_ok=True)
    model.get_booster().save_model(str(champ_dir / "model.json"))
    meta = {
        "model_version": f"{MODEL_NAME}/v{version}",
        "features": FEATURES,
        "trained_at": datetime.now(UTC).isoformat(),
        **{k: round(v, 6) for k, v in metrics.items()},
    }
    (champ_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return champ_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Train DosaDash demand forecaster")
    parser.add_argument("--synthetic", action="store_true", help="train on datagen output")
    parser.add_argument("--users", type=int, default=500)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--valid-days", type=int, default=28)
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--tracking-uri", default="sqlite:///packages/ml/mlflow.db")
    parser.add_argument("--export-dir", default="packages/ml/artifacts")
    args = parser.parse_args()
    if not args.synthetic:
        parser.error("only --synthetic is supported until the DB extractor lands")

    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("demand-forecast")

    sales, metas, start, end = synthetic_daily_sales(
        users=args.users, days=args.days, seed=args.seed
    )
    frame = add_features(make_dense_daily(sales, metas, start, end))
    model, metrics = train(frame, valid_days=args.valid_days, n_estimators=args.n_estimators)
    params: dict[str, object] = {
        "users": args.users,
        "days": args.days,
        "seed": args.seed,
        "valid_days": args.valid_days,
        "n_estimators": args.n_estimators,
        "features": ",".join(FEATURES),
    }
    version, promoted = register_and_maybe_promote(model, metrics, params=params)
    line = (
        f"v{version} promoted={promoted} "
        f"WAPE={metrics['valid_wape']:.3f} (naive lag-7 {metrics['baseline_lag7_wape']:.3f}) "
        f"MAE={metrics['valid_mae']:.3f} (naive {metrics['baseline_lag7_mae']:.3f})"
    )
    print(line)
    if promoted:
        champ_dir = export_champion(model, metrics, version, Path(args.export_dir))
        print(f"exported champion → {champ_dir}")


if __name__ == "__main__":
    main()
