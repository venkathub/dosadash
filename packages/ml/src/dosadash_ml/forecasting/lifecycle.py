"""Offline rolling-origin evaluation and atomic, reversible artifact releases.

Run `python -m dosadash_ml.forecasting.lifecycle demo --store /tmp/forecast-demo`.
This never changes production artifacts or aliases. Training stays off the VPS.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import xgboost as xgb

from dosadash_ml.forecasting.features import FEATURES, ItemMeta, add_features, make_dense_daily
from dosadash_ml.forecasting.monitoring import wape
from dosadash_ml.forecasting.predict import ChampionModel, forecast_next_days, load_champion


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fit(dense: pd.DataFrame, trees: int) -> ChampionModel:
    frame = add_features(dense)
    if len(frame) < 28:
        raise ValueError("at least 28 usable training rows required")
    model = xgb.XGBRegressor(
        n_estimators=trees,
        max_depth=3,
        learning_rate=0.1,
        objective="reg:squarederror",
        tree_method="hist",
        random_state=42,
        n_jobs=1,
    )
    model.fit(frame[FEATURES], frame["qty"])
    return ChampionModel(
        model.get_booster(),
        f"candidate-trees-{trees}",
        {
            "features": FEATURES,
            "trained_through": str(max(dense["date"])),
            "trees": trees,
        },
    )


def score_window(
    model: ChampionModel,
    dense: pd.DataFrame,
    metas: list[ItemMeta],
    start: date,
    horizon: int,
) -> dict:
    # Features only see data before the origin. Subsequent lags use predictions,
    # matching production's recursive forecast, never held-out actuals.
    before = dense[dense["date"] < start]
    history = {m.item_id: before[before.item_id == m.item_id].qty.tolist() for m in metas}
    rows = forecast_next_days(
        model, history, {m.item_id: m for m in metas}, start=start, horizon=horizon
    )
    truth = {(int(r.item_id), r.date): float(r.qty) for r in dense.itertuples()}
    actual = [truth[r.item_id, r.date] for r in rows]
    predicted = [r.predicted_qty for r in rows]
    naive_history = {k: list(v) for k, v in history.items()}
    naive = []
    for row in rows:
        series = naive_history[row.item_id]
        value = series[-7]
        naive.append(value)
        series.append(value)
    return {
        "origin": start.isoformat(),
        "horizon": horizon,
        "n": len(rows),
        "wape": wape(actual, predicted),
        "baseline_wape": wape(actual, naive),
        "absolute_error": sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)),
        "baseline_error": sum(abs(a - p) for a, p in zip(actual, naive, strict=True)),
        "actual_sum": sum(actual),
    }


def promotion_decision(report: dict) -> bool:
    folds = report.get("validation", [])
    if len(folds) < 3:
        return False
    values = [
        f.get(k) for f in folds for k in ("candidate_wape", "incumbent_wape", "baseline_wape")
    ]
    if any(v is None or not math.isfinite(v) or v < 0 for v in values):
        return False
    weights = [f["actual_sum"] for f in folds]
    if any(not math.isfinite(w) or w <= 0 for w in weights):
        return False

    def average(key: str) -> float:
        return sum(f[key] * w for f, w in zip(folds, weights, strict=True)) / sum(weights)

    return (
        average("candidate_wape") < average("incumbent_wape") * 0.98
        and average("candidate_wape") < average("baseline_wape")
        and all(f["candidate_wape"] <= f["incumbent_wape"] * 1.05 for f in folds)
    )


def evaluate(
    dense: pd.DataFrame, metas: list[ItemMeta], *, horizon: int = 14
) -> tuple[dict, ChampionModel, ChampionModel]:
    end = max(dense["date"])
    origins = [end - timedelta(days=horizon * n - 1) for n in (4, 3, 2)]
    train = dense[dense["date"] < origins[0]]
    incumbent = fit(train, trees=2)
    candidate = fit(train, trees=80)
    folds = []
    for origin in origins:
        inc = score_window(incumbent, dense, metas, origin, horizon)
        cand = score_window(candidate, dense, metas, origin, horizon)
        folds.append(
            {
                "origin": origin.isoformat(),
                "horizon": horizon,
                "candidate_wape": cand["wape"],
                "incumbent_wape": inc["wape"],
                "baseline_wape": cand["baseline_wape"],
                "actual_sum": cand["actual_sum"],
            }
        )
    report = {
        "schema_version": 1,
        "source_sha256": hashlib.sha256(
            b"".join(
                Path(__file__).with_name(name).read_bytes()
                for name in ["lifecycle.py", "predict.py", "features.py", "monitoring.py"]
            )
        ).hexdigest(),
        "runtime": {"xgboost": xgb.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "evidence": "synthetic_portfolio_demo",
        "dataset_sha256": hashlib.sha256(dense.to_csv(index=False).encode()).hexdigest(),
        "trained_through": incumbent.meta["trained_through"],
        "validation": folds,
        "policy": "3 folds; 2% improvement; beat lag7; <=5% fold regression",
    }
    promoted = promotion_decision(report)
    report["decision"] = "promote" if promoted else "retain"
    # Final period is opened only AFTER the validation decision. It never changes it.
    report["final_test"] = score_window(
        candidate if promoted else incumbent,
        dense,
        metas,
        end - timedelta(days=horizon - 1),
        horizon,
    )
    report["limitations"] = [
        "Synthetic data, not independent real-world accuracy.",
        "Fixed candidates; no tuning on the final period.",
        "Production deployment requires artifact PR review and CI.",
    ]
    return report, incumbent, candidate


@contextmanager
def store_lock(root: Path) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def atomic_json(path: Path, value: dict) -> None:
    # Same-filesystem replace makes the active version and audit history atomic.
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def release(root: Path, model: ChampionModel, report: dict) -> str:
    """Save immutable model/evidence bundle. Caller holds store lock."""
    version = str(uuid4())
    folder = root / "releases" / version
    champion = folder / "forecast" / "champion"
    champion.mkdir(parents=True)
    model.booster.save_model(str(champion / "model.json"))
    atomic_json(champion / "meta.json", {**model.meta, "model_version": version})
    atomic_json(folder / "report.json", report)
    atomic_json(
        folder / "hashes.json",
        {
            str(p.relative_to(folder)): digest(p)
            for p in [champion / "model.json", champion / "meta.json", folder / "report.json"]
        },
    )
    verify_release(root, version)
    return version


def verify_release(root: Path, version: str) -> Path:
    if str(UUID(version)) != version:
        raise ValueError("invalid release id")
    folder = root / "releases" / version
    hashes = json.loads((folder / "hashes.json").read_text())
    required = {"forecast/champion/model.json", "forecast/champion/meta.json", "report.json"}
    if set(hashes) != required or any(digest(folder / name) != sha for name, sha in hashes.items()):
        raise ValueError("release artifact integrity check failed")
    load_champion(folder)  # feature schema / real XGBoost loading check before pointer mutation
    return folder


def rollback(root: Path, reason: str) -> dict:
    if len(reason.strip()) < 10:
        raise ValueError("document a rollback reason of at least ten characters")
    with store_lock(root):
        state = json.loads((root / "active.json").read_text())
        previous = state.get("previous")
        if not previous:
            raise ValueError("no previous release to restore")
        verify_release(root, previous)
        state["history"].append(
            {
                "action": "rollback",
                "from": state["active"],
                "to": previous,
                "reason": reason,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        state["active"], state["previous"] = previous, None  # no automatic oscillation
        atomic_json(root / "active.json", state)
        return state


def demo(root: Path) -> dict:
    start = date(2026, 1, 1)
    metas = [ItemMeta(item_id=i, category="Dosa", is_veg=True) for i in (1, 2, 3)]
    rng = np.random.default_rng(7)
    sales = pd.DataFrame(
        [
            {
                "item_id": m.item_id,
                "date": start + timedelta(days=d),
                "qty": max(
                    0,
                    30
                    + 8 * m.item_id
                    + 12 * ((start + timedelta(days=d)).weekday() >= 5)
                    + float(rng.normal(0, 9)),
                ),
            }
            for m in metas
            for d in range(196)
        ]
    )
    dense = make_dense_daily(sales, metas, start, start + timedelta(days=195))
    report, incumbent, candidate = evaluate(dense, metas)
    with store_lock(root):
        if (root / "active.json").exists():
            raise ValueError("demo store already exists; use a new directory")
        initial = release(
            root, incumbent, {"evidence": "synthetic_portfolio_demo", "role": "incumbent"}
        )
        challenger = release(root, candidate, report)
        promoted = report["decision"] == "promote"
        state = {
            "active": challenger if promoted else initial,
            "previous": initial if promoted else None,
            "history": [
                {
                    "action": report["decision"],
                    "candidate": challenger,
                    "at": datetime.now(UTC).isoformat(),
                }
            ],
        }
        atomic_json(root / "active.json", state)
        atomic_json(root / "report.json", report)
    return {"store": str(root), "decision": report["decision"], "active": state["active"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "rollback", "inspect"])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.command == "demo":
        result = demo(args.store)
    elif args.command == "rollback":
        result = rollback(args.store, args.reason)
    else:
        with store_lock(args.store):
            result = json.loads((args.store / "active.json").read_text())
            verify_release(args.store, result["active"])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
