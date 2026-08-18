"""ETA feature engineering — pure Python (no pandas: this path runs in the
ai service at checkout time).

Times are restaurant-local (Asia/Kolkata): the synthetic world's naive
timestamps are IST by definition, and callers must convert real UTC
timestamps before building rows.
"""

from datetime import datetime

from dosadash_ml.datagen import is_festival_day

ETA_FEATURES = [
    "max_prep",
    "total_qty",
    "n_lines",
    "hour",
    "dow",
    "is_weekend",
    "is_festival",
]

PEAK_HOURS = {12, 13, 19, 20, 21}


def eta_feature_row(*, max_prep: int, total_qty: int, n_lines: int, when: datetime) -> list[float]:
    """One scoring/training row. `when` must be restaurant-local (IST)."""
    day = when.date()
    return [
        float(max_prep),
        float(total_qty),
        float(n_lines),
        float(when.hour),
        float(when.weekday()),
        1.0 if when.weekday() >= 5 else 0.0,
        1.0 if is_festival_day(day) else 0.0,
    ]


def heuristic_eta_minutes(*, max_prep: int, total_qty: int, when: datetime) -> int:
    """Model-free fallback (api uses this when the ai service is down).

    Mirrors the expected value of the synthetic delivery process: prep +
    basket handling + mean ride time + peak/weekend/festival load.
    """
    minutes = max_prep + 2.0 * total_qty + 13.0
    if when.hour in PEAK_HOURS:
        minutes += 8.0
    if when.weekday() >= 5:
        minutes += 5.0
    if is_festival_day(when.date()):
        minutes += 6.0
    return max(18, min(90, round(minutes)))
