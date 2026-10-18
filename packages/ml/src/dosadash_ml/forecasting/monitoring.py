"""Small deterministic forecast-health policy; no training dependencies on the VPS."""

import math
from collections.abc import Sequence


def wape(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    if len(actual) != len(predicted) or not actual:
        return None
    if any(not math.isfinite(v) or v < 0 for v in [*actual, *predicted]):
        raise ValueError("forecast observations must be finite and nonnegative")
    total = sum(actual)
    return (
        sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)) / total if total else None
    )


def degradation(
    baseline_actual: Sequence[float],
    baseline_predicted: Sequence[float],
    recent_actual: Sequence[float],
    recent_predicted: Sequence[float],
    *,
    complete_days: int,
    model_versions: Sequence[str],
) -> dict:
    old = wape(baseline_actual, baseline_predicted)
    recent = wape(recent_actual, recent_predicted)
    enough = (
        complete_days >= 28
        and old is not None
        and recent is not None
        and len(set(model_versions)) == 1
    )
    # Require both absolute and relative deterioration to avoid zero-baseline noise.
    triggered = bool(enough and recent > max(0.30, old * 1.25, old + 0.05))
    return {
        "status": "degraded" if triggered else "healthy" if enough else "insufficient_data",
        "baseline_wape": old,
        "recent_wape": recent,
        "complete_days": complete_days,
        "model_versions": sorted(set(model_versions)),
        "action": "prepare_candidate_offline" if triggered else "observe",
        "policy": {
            "relative": 1.25,
            "absolute": 0.30,
            "minimum_increase": 0.05,
            "window_days": 14,
            "minimum_complete_days": 28,
        },
    }
