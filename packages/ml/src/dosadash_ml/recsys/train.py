"""Train the ALS recommender + manage its MLflow registry entry (local/CI).

    uv run python -m dosadash_ml.recsys.train --synthetic \
        [--tracking-uri sqlite:///packages/ml/mlflow.db] \
        [--export-dir packages/ml/artifacts]

Evaluation: rank the held-out window (last `valid_days` of orders) per user;
Recall@K / MAP@K vs a popularity baseline, plus tail Recall@K (holdout items
OUTSIDE the top-10 bestsellers — where "just show bestsellers" scores zero by
construction; this is where personalization earns its keep on a 52-item
catalog). Promotion: `champion` alias moves iff Recall@K improves — same
contract as forecasting/ETA.

Confidences are log1p-scaled by default (a year of repeat orders is heavy-
tailed; raw counts let one favorite dish drown the rest of the taste vector).
The scale is recorded in meta.json and mirrored by the serve-time fold-in.

Only ITEM factors are exported: serving folds live DB history into the
factor space (see predict.py), so synthetic user ids never leak anywhere.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import numpy as np
from implicit.als import AlternatingLeastSquares
from mlflow.tracking import MlflowClient

from dosadash_ml.recsys.dataset import InteractionDataset, build_interactions

MODEL_NAME = "dosadash-recsys"
CHAMPION = "champion"
EVAL_K = 10
TAIL_AFTER_TOP = 10  # tail metric ignores the top-N bestsellers


def scale_confidences(data: InteractionDataset, count_scale: str) -> InteractionDataset:
    """Return a dataset whose train matrix carries scaled confidences."""
    if count_scale == "raw":
        return data
    if count_scale != "log1p":
        raise ValueError(f"unknown count_scale: {count_scale}")
    scaled = data.train.copy().astype(float)
    scaled.data = np.log1p(scaled.data)
    return InteractionDataset(
        user_phones=data.user_phones,
        item_names=data.item_names,
        train=scaled.tocsr(),
        holdout=data.holdout,
    )


def train_als(
    data: InteractionDataset,
    *,
    factors: int = 64,
    regularization: float = 0.5,
    alpha: float = 5.0,
    iterations: int = 30,
    seed: int = 42,
) -> AlternatingLeastSquares:
    model = AlternatingLeastSquares(
        factors=factors,
        regularization=regularization,
        alpha=alpha,
        iterations=iterations,
        random_state=seed,
        use_gpu=False,
    )
    model.fit(data.train, show_progress=False)
    return model


def _rank_metrics(ranked_items: list[int], actual: set[int], k: int) -> tuple[float, float]:
    """(recall@k, ap@k) for one user."""
    hits, precision_sum = 0, 0.0
    for rank, item in enumerate(ranked_items[:k], start=1):
        if item in actual:
            hits += 1
            precision_sum += hits / rank
    denom = min(len(actual), k)
    return hits / denom, precision_sum / denom


def evaluate(
    model: AlternatingLeastSquares, data: InteractionDataset, *, k: int = EVAL_K
) -> dict[str, float]:
    """Model vs popularity baseline on the held-out window. Users with no
    holdout activity (or no training history to fold from) are skipped —
    cold-start is served by a different strategy in prod.

    Tail Recall@K counts only holdout items outside the top bestsellers:
    the popularity baseline scores 0.0 there by construction, so this is the
    cleanest measure of what personalization adds on a small catalog."""
    popularity_rank = list(np.asarray(data.train.sum(axis=0)).ravel().argsort()[::-1])
    head = set(popularity_rank[:TAIL_AFTER_TOP])
    recalls, aps, pop_recalls, pop_aps, evaluated = [], [], [], [], 0
    tail_hits, tail_total = 0, 0
    for user in range(data.train.shape[0]):
        actual = data.holdout[user]
        if not actual or data.train[user].nnz == 0:
            continue
        evaluated += 1
        ids, _ = model.recommend(user, data.train[user], N=k, filter_already_liked_items=False)
        recall, ap = _rank_metrics(list(ids), actual, k)
        pop_recall, pop_ap = _rank_metrics(popularity_rank, actual, k)
        recalls.append(recall)
        aps.append(ap)
        pop_recalls.append(pop_recall)
        pop_aps.append(pop_ap)
        actual_tail = actual - head
        tail_hits += len(set(ids[:k]) & actual_tail)
        tail_total += len(actual_tail)
    return {
        f"recall_at_{k}": float(np.mean(recalls)),
        f"map_at_{k}": float(np.mean(aps)),
        f"popularity_recall_at_{k}": float(np.mean(pop_recalls)),
        f"popularity_map_at_{k}": float(np.mean(pop_aps)),
        f"tail_recall_at_{k}": float(tail_hits / tail_total) if tail_total else 0.0,
        # popularity's tail recall is 0.0 by construction — not logged
        "evaluated_users": float(evaluated),
        "train_users": float(data.train.shape[0]),
        "train_items": float(data.train.shape[1]),
        "train_interactions": float(data.train.nnz),
    }


def _current_champion_recall(client: MlflowClient) -> float | None:
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except Exception:
        return None
    tag = version.tags.get(f"recall_at_{EVAL_K}")
    return float(tag) if tag is not None else None


def register_and_maybe_promote(
    metrics: dict[str, float], *, params: dict[str, object], force: bool = False
) -> tuple[str, bool]:
    client = MlflowClient()
    with mlflow.start_run(run_name=f"recsys-{datetime.now(UTC):%Y%m%d-%H%M}") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        # Factors are exported as plain npy (no mlflow flavor for implicit);
        # the registry entry versions the run + metrics for the promote gate.
        version = client.create_model_version(
            name=_ensure_registered(client),
            source=run.info.artifact_uri,
            run_id=run.info.run_id,
        ).version
    key = f"recall_at_{EVAL_K}"
    client.set_model_version_tag(MODEL_NAME, version, key, f"{metrics[key]:.6f}")
    incumbent = _current_champion_recall(client)
    # --force-promote exists for catalog changes: the incumbent's recall was
    # measured on a different item set, so the comparison is meaningless AND
    # its exported factors can no longer serve the live menu.
    promote = force or incumbent is None or metrics[key] >= incumbent
    if promote:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    return str(version), promote


def _ensure_registered(client: MlflowClient) -> str:
    try:
        client.create_registered_model(MODEL_NAME)
    except Exception:  # already exists
        pass
    return MODEL_NAME


def export_champion(
    model: AlternatingLeastSquares,
    data: InteractionDataset,
    metrics: dict[str, float],
    version: str,
    *,
    alpha: float,
    regularization: float,
    count_scale: str,
    export_dir: Path,
) -> Path:
    champ_dir = export_dir / "recsys" / "champion"
    champ_dir.mkdir(parents=True, exist_ok=True)
    item_factors = np.asarray(model.item_factors, dtype=np.float32)
    np.save(champ_dir / "item_factors.npy", item_factors)
    order_counts = np.asarray(data.train.sum(axis=0)).ravel()
    popularity = [data.item_names[i] for i in order_counts.argsort()[::-1]]
    meta = {
        "model_version": f"{MODEL_NAME}/v{version}",
        "item_names": data.item_names,
        "popularity": popularity,
        "alpha": alpha,
        "regularization": regularization,
        "count_scale": count_scale,  # serve-time fold-in must mirror this
        "factors": int(item_factors.shape[1]),
        "trained_at": datetime.now(UTC).isoformat(),
        **{k: round(v, 6) for k, v in metrics.items()},
    }
    (champ_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return champ_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Train DosaDash ALS recommender")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--users", type=int, default=500)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--valid-days", type=int, default=28)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--factors", type=int, default=64)
    parser.add_argument("--regularization", type=float, default=0.5)
    parser.add_argument("--alpha", type=float, default=5.0)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--count-scale", choices=("log1p", "raw"), default="log1p")
    parser.add_argument(
        "--force-promote",
        action="store_true",
        help="promote regardless of incumbent recall — required after catalog changes",
    )
    parser.add_argument("--tracking-uri", default="sqlite:///packages/ml/mlflow.db")
    parser.add_argument("--export-dir", default="packages/ml/artifacts")
    args = parser.parse_args()
    if not args.synthetic:
        parser.error("only --synthetic is supported until the DB extractor lands")

    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("recsys")

    data = scale_confidences(
        build_interactions(
            users=args.users, days=args.days, valid_days=args.valid_days, seed=args.seed
        ),
        args.count_scale,
    )
    model = train_als(
        data,
        factors=args.factors,
        regularization=args.regularization,
        alpha=args.alpha,
        iterations=args.iterations,
        seed=args.seed,
    )
    metrics = evaluate(model, data)
    params: dict[str, object] = {
        "users": args.users,
        "days": args.days,
        "valid_days": args.valid_days,
        "seed": args.seed,
        "factors": args.factors,
        "regularization": args.regularization,
        "alpha": args.alpha,
        "iterations": args.iterations,
        "count_scale": args.count_scale,
    }
    version, promoted = register_and_maybe_promote(metrics, params=params, force=args.force_promote)
    k = EVAL_K
    print(
        f"v{version} promoted={promoted} "
        f"Recall@{k}={metrics[f'recall_at_{k}']:.3f} "
        f"(popularity {metrics[f'popularity_recall_at_{k}']:.3f}) "
        f"MAP@{k}={metrics[f'map_at_{k}']:.3f} "
        f"(popularity {metrics[f'popularity_map_at_{k}']:.3f}) "
        f"tailRecall@{k}={metrics[f'tail_recall_at_{k}']:.3f} (popularity 0.000)"
    )
    if promoted:
        champ_dir = export_champion(
            model,
            data,
            metrics,
            version,
            alpha=args.alpha,
            regularization=args.regularization,
            count_scale=args.count_scale,
            export_dir=Path(args.export_dir),
        )
        print(f"exported champion → {champ_dir}")


if __name__ == "__main__":
    main()
