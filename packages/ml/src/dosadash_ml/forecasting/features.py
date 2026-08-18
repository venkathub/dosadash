"""Feature engineering for the demand forecaster.

One global XGBoost model over (item, day) rows; per-item level is carried by
the lag/rolling features. Festival seasonality comes from
`datagen.category_multiplier` — the exact function that generates the
synthetic demand — so train/score features are definitionally in sync.
"""

from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta

import pandas as pd

from dosadash_ml.datagen import category_multiplier

FEATURES = ["lag_7", "lag_14", "ma_7", "dow", "is_weekend", "festival_mult"]
TARGET = "qty"
MIN_HISTORY_DAYS = 14  # lag_14 is the longest lookback


@dataclass(frozen=True)
class ItemMeta:
    item_id: int
    category: str
    is_veg: bool


def make_dense_daily(
    sales: pd.DataFrame, items: list[ItemMeta], start: date_type, end: date_type
) -> pd.DataFrame:
    """Zero-filled (item × day) grid from sparse sales rows.

    `sales` columns: item_id, date, qty. Days without orders are real zeros —
    the model must learn them, not skip them.
    """
    all_days = pd.DataFrame(
        {"date": [start + timedelta(days=i) for i in range((end - start).days + 1)]}
    )
    meta = pd.DataFrame(
        {
            "item_id": [m.item_id for m in items],
            "category": [m.category for m in items],
            "is_veg": [m.is_veg for m in items],
        }
    )
    grid = meta.merge(all_days, how="cross")
    dense = grid.merge(sales, on=["item_id", "date"], how="left")
    dense["qty"] = dense["qty"].fillna(0.0).astype(float)
    return dense.sort_values(["item_id", "date"]).reset_index(drop=True)


def add_features(dense: pd.DataFrame) -> pd.DataFrame:
    """Lag/rolling/calendar features on a dense grid; drops warm-up rows."""
    df = dense.copy()
    grp = df.groupby("item_id")["qty"]
    df["lag_7"] = grp.shift(7)
    df["lag_14"] = grp.shift(14)
    df["ma_7"] = grp.shift(1).rolling(7).mean().reset_index(level=0, drop=True)
    dts = pd.to_datetime(df["date"])
    df["dow"] = dts.dt.dayofweek.astype(float)
    df["is_weekend"] = (df["dow"] >= 5).astype(float)
    df["festival_mult"] = [
        category_multiplier(c, v, d)
        for c, v, d in zip(df["category"], df["is_veg"], dts.dt.date, strict=True)
    ]
    return df.dropna(subset=["lag_7", "lag_14", "ma_7"]).reset_index(drop=True)


def feature_row(series: list[float], meta: ItemMeta, day: date_type) -> list[float]:
    """Single scoring row from a trailing qty series (recursive prediction).

    Must mirror `add_features` exactly — any skew here is silent model damage.
    """
    padded = ([0.0] * max(0, MIN_HISTORY_DAYS - len(series))) + series
    return [
        padded[-7],  # lag_7
        padded[-14],  # lag_14
        sum(padded[-7:]) / 7.0,  # ma_7
        float(day.weekday()),  # dow
        1.0 if day.weekday() >= 5 else 0.0,  # is_weekend
        category_multiplier(meta.category, meta.is_veg, day),  # festival_mult
    ]
