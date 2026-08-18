"""Train the ETA regressor + manage its MLflow registry entry (local/CI only).

    uv run python -m dosadash_ml.eta.train --synthetic \
        [--tracking-uri sqlite:///packages/ml/mlflow.db] \
        [--export-dir packages/ml/artifacts]

Labels come from the synthetic world's `delivered_minutes` (same process
that backfills orders.delivered_at in the seeded DB). Promotion: `champion`
alias moves iff validation MAE improves. Baseline = the model-free
`heuristic_eta_minutes` the api falls back to.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import numpy as np
import xgboost as xgb
from mlflow.tracking import MlflowClient

from dosadash_ml.datagen import MENU_ITEMS, generate_orders, generate_users
from dosadash_ml.eta.features import ETA_FEATURES, eta_feature_row, heuristic_eta_minutes

MODEL_NAME = "dosadash-eta"
CHAMPION = "champion"


def synthetic_eta_dataset(
    *, users: int = 500, days: int = 365, seed: int = 42
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(X, y, heuristic_pred, order_dates) from datagen. Naive timestamps are IST."""
    prep_by_name = {m.name: m.prep_minutes for m in MENU_ITEMS}
    orders = generate_orders(generate_users(n=users, seed=seed), days=days, seed=seed)
    rows, labels, heuristic, dates = [], [], [], []
    for order in orders:
        max_prep = max(prep_by_name[line.item_name] for line in order.items)
        total_qty = sum(line.qty for line in order.items)
        rows.append(
            eta_feature_row(
                max_prep=max_prep,
                total_qty=total_qty,
                n_lines=len(order.items),
                when=order.placed_at,
            )
        )
        labels.append(float(order.delivered_minutes))
        heuristic.append(
            float(
                heuristic_eta_minutes(max_prep=max_prep, total_qty=total_qty, when=order.placed_at)
            )
        )
        dates.append(order.placed_at.date().toordinal())
    return (
        np.array(rows, dtype=float),
        np.array(labels, dtype=float),
        np.array(heuristic, dtype=float),
        np.array(dates, dtype=int),
    )


def train_eta(
    x: np.ndarray,
    y: np.ndarray,
    heuristic: np.ndarray,
    dates: np.ndarray,
    *,
    valid_days: int = 28,
    n_estimators: int = 300,
) -> tuple[xgb.XGBRegressor, dict[str, float]]:
    """Time-split train/valid; metrics vs the heuristic fallback baseline."""
    cutoff = dates.max() - valid_days
    is_valid = dates > cutoff
    model = xgb.XGBRegressor(
        n_estimators=n_estimators,
        max_depth=5,
        learning_rate=0.06,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        tree_method="hist",
        random_state=42,
    )
    model.fit(x[~is_valid], y[~is_valid])
    pred = model.predict(x[is_valid])
    err = np.abs(y[is_valid] - pred)
    base_err = np.abs(y[is_valid] - heuristic[is_valid])
    return model, {
        "valid_mae": float(err.mean()),
        "valid_p90_abs_err": float(np.percentile(err, 90)),
        "baseline_heuristic_mae": float(base_err.mean()),
        "baseline_heuristic_p90_abs_err": float(np.percentile(base_err, 90)),
        "train_rows": float((~is_valid).sum()),
        "valid_rows": float(is_valid.sum()),
    }


def _current_champion_mae(client: MlflowClient) -> float | None:
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except Exception:
        return None
    tag = version.tags.get("mae")
    return float(tag) if tag is not None else None


def register_and_maybe_promote(
    model: xgb.XGBRegressor, metrics: dict[str, float], *, params: dict[str, object]
) -> tuple[str, bool]:
    client = MlflowClient()
    with mlflow.start_run(run_name=f"eta-{datetime.now(UTC):%Y%m%d-%H%M}"):
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        info = mlflow.xgboost.log_model(model, name="model", registered_model_name=MODEL_NAME)
    version = str(info.registered_model_version)
    client.set_model_version_tag(MODEL_NAME, version, "mae", f"{metrics['valid_mae']:.6f}")
    incumbent = _current_champion_mae(client)
    promote = incumbent is None or metrics["valid_mae"] <= incumbent
    if promote:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    return version, promote


def export_champion(
    model: xgb.XGBRegressor, metrics: dict[str, float], version: str, export_dir: Path
) -> Path:
    champ_dir = export_dir / "eta" / "champion"
    champ_dir.mkdir(parents=True, exist_ok=True)
    model.get_booster().save_model(str(champ_dir / "model.json"))
    meta = {
        "model_version": f"{MODEL_NAME}/v{version}",
        "features": ETA_FEATURES,
        "trained_at": datetime.now(UTC).isoformat(),
        **{k: round(v, 6) for k, v in metrics.items()},
    }
    (champ_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return champ_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Train DosaDash ETA regressor")
    parser.add_argument("--synthetic", action="store_true")
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
    mlflow.set_experiment("eta")

    x, y, heuristic, dates = synthetic_eta_dataset(users=args.users, days=args.days, seed=args.seed)
    model, metrics = train_eta(
        x, y, heuristic, dates, valid_days=args.valid_days, n_estimators=args.n_estimators
    )
    params: dict[str, object] = {
        "users": args.users,
        "days": args.days,
        "seed": args.seed,
        "valid_days": args.valid_days,
        "n_estimators": args.n_estimators,
        "features": ",".join(ETA_FEATURES),
    }
    version, promoted = register_and_maybe_promote(model, metrics, params=params)
    p90 = metrics["valid_p90_abs_err"]
    base_p90 = metrics["baseline_heuristic_p90_abs_err"]
    print(
        f"v{version} promoted={promoted} "
        f"MAE={metrics['valid_mae']:.2f}m (heuristic {metrics['baseline_heuristic_mae']:.2f}m) "
        f"P90={p90:.2f}m (heuristic {base_p90:.2f}m)"
    )
    if promoted:
        champ_dir = export_champion(model, metrics, version, Path(args.export_dir))
        print(f"exported champion → {champ_dir}")


if __name__ == "__main__":
    main()
