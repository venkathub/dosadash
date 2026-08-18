"""Champion-model scoring (VPS-safe: xgboost + stdlib only, no MLflow).

Artifacts layout (exported by train.py, baked into the worker image):
    {model_dir}/forecast/champion/model.json   — xgboost booster
    {model_dir}/forecast/champion/meta.json    — model_version, features, metrics
"""

import json
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

from dosadash_ml.forecasting.features import FEATURES, ItemMeta, feature_row


@dataclass(frozen=True)
class ChampionModel:
    booster: xgb.Booster
    version: str
    meta: dict[str, Any]


@dataclass(frozen=True)
class ForecastRow:
    item_id: int
    date: date_type
    predicted_qty: float


def load_champion(model_dir: str | Path, name: str = "forecast") -> ChampionModel:
    champ_dir = Path(model_dir) / name / "champion"
    meta = json.loads((champ_dir / "meta.json").read_text())
    if meta.get("features") != FEATURES:
        raise ValueError(
            f"champion features {meta.get('features')} != code {FEATURES} — retrain/re-export"
        )
    booster = xgb.Booster()
    booster.load_model(str(champ_dir / "model.json"))
    return ChampionModel(booster=booster, version=meta["model_version"], meta=meta)


def forecast_next_days(
    model: ChampionModel,
    history: dict[int, list[float]],
    metas: dict[int, ItemMeta],
    *,
    start: date_type | None = None,
    horizon: int = 14,
) -> list[ForecastRow]:
    """Recursive multi-step forecast: each predicted day is appended to the
    trailing series so lag_7 stays meaningful beyond a 7-day horizon.

    `history[item_id]` = daily qty series ending yesterday (dense, zeros
    included). Items with short history are zero-padded by `feature_row`.
    """
    start = start or (date_type.today())
    series = {item_id: list(vals) for item_id, vals in history.items()}
    item_ids = sorted(set(series) & set(metas))
    rows: list[ForecastRow] = []
    for h in range(horizon):
        day = start + timedelta(days=h)
        matrix = np.array([feature_row(series[i], metas[i], day) for i in item_ids], dtype=float)
        preds = model.booster.predict(xgb.DMatrix(matrix, feature_names=FEATURES))
        for item_id, pred in zip(item_ids, preds, strict=True):
            qty = max(0.0, float(pred))
            series[item_id].append(qty)
            rows.append(ForecastRow(item_id=item_id, date=day, predicted_qty=round(qty, 2)))
    return rows
