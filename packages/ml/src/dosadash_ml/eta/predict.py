"""ETA champion scoring — xgboost + stdlib only (loaded by the ai service).

Artifacts layout: {model_dir}/eta/champion/{model.json, meta.json}
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

from dosadash_ml.eta.features import ETA_FEATURES, eta_feature_row


@dataclass(frozen=True)
class EtaChampion:
    booster: xgb.Booster
    version: str
    meta: dict[str, Any]


def load_eta_champion(model_dir: str | Path) -> EtaChampion:
    champ_dir = Path(model_dir) / "eta" / "champion"
    meta = json.loads((champ_dir / "meta.json").read_text())
    if meta.get("features") != ETA_FEATURES:
        raise ValueError(
            f"eta champion features {meta.get('features')} != code {ETA_FEATURES} — retrain"
        )
    booster = xgb.Booster()
    booster.load_model(str(champ_dir / "model.json"))
    return EtaChampion(booster=booster, version=meta["model_version"], meta=meta)


def predict_eta_minutes(
    model: EtaChampion, *, max_prep: int, total_qty: int, n_lines: int, when: datetime
) -> int:
    """Predicted delivery minutes for one order (`when` in restaurant-local time)."""
    row = eta_feature_row(max_prep=max_prep, total_qty=total_qty, n_lines=n_lines, when=when)
    matrix = xgb.DMatrix(np.array([row], dtype=float), feature_names=ETA_FEATURES)
    pred = float(model.booster.predict(matrix)[0])
    return max(15, min(120, round(pred)))
