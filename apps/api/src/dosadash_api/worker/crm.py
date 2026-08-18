"""RFM + churn + LTV scoring (pure functions — the Celery task feeds SQL
aggregates in and upserts the output).

Tier semantics (docs/04 O6): quintile R/F scores over the active base, then
named tiers the owner can act on (win-back coupons target AT_RISK/LOST).
Churn risk is an explainable decay: how overdue is this user vs their own
ordering rhythm — 0 right after an order, →1 as silence stretches past ~2×
their usual gap.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_GAP_DAYS = 30.0  # fallback rhythm for single-order users
NEW_USER_WINDOW_DAYS = 30


@dataclass(frozen=True)
class UserAggregate:
    """One user's order stats (last 365 days, CANCELLED excluded)."""

    user_id: int
    n_orders: int
    total_spend: float
    first_order_at: datetime
    last_order_at: datetime


@dataclass(frozen=True)
class SegmentScore:
    user_id: int
    rfm_tier: str
    churn_risk: float
    ltv: float


def churn_risk(recency_days: float, avg_gap_days: float) -> float:
    """1 - exp(-recency / 2·gap): 0 fresh, ~0.4 at their usual gap, →1 late."""
    gap = max(avg_gap_days, 3.5)
    return round(min(1.0, 1.0 - math.exp(-recency_days / (2.0 * gap))), 4)


def _quintile_score(value: float, sorted_values: list[float], *, reverse: bool = False) -> int:
    """1–5 by position among peers. reverse=True → smaller value scores higher
    (recency: fewer days since last order is better)."""
    if not sorted_values:
        return 3
    rank = sum(1 for v in sorted_values if v <= value) / len(sorted_values)
    score = 1 + min(4, int(rank * 5))
    return 6 - score if reverse else score


def _tier(r: int, f: int, *, days_since_first: float, n_orders: int) -> str:
    if n_orders <= 2 and days_since_first <= NEW_USER_WINDOW_DAYS:
        return "NEW"
    if r >= 4 and f >= 4:
        return "CHAMPION"
    if f >= 4:
        return "LOYAL"
    if r >= 4 and f >= 2:
        return "POTENTIAL"
    if r <= 2 and f >= 3:
        return "AT_RISK"
    if r <= 1:
        return "LOST"
    return "REGULAR"


def score_segments(
    aggregates: list[UserAggregate], *, now: datetime | None = None
) -> list[SegmentScore]:
    """Quintile R/F over the whole base + per-user churn/LTV, one pass."""
    if not aggregates:
        return []
    now = now or datetime.now(UTC)

    recencies = sorted((now - a.last_order_at).total_seconds() / 86400 for a in aggregates)
    frequencies = sorted(float(a.n_orders) for a in aggregates)

    scores: list[SegmentScore] = []
    for agg in aggregates:
        recency = (now - agg.last_order_at).total_seconds() / 86400
        lifespan = (agg.last_order_at - agg.first_order_at).total_seconds() / 86400
        avg_gap = lifespan / (agg.n_orders - 1) if agg.n_orders > 1 else DEFAULT_GAP_DAYS
        r = _quintile_score(recency, recencies, reverse=True)
        f = _quintile_score(float(agg.n_orders), frequencies)
        days_since_first = (now - agg.first_order_at).total_seconds() / 86400
        scores.append(
            SegmentScore(
                user_id=agg.user_id,
                rfm_tier=_tier(r, f, days_since_first=days_since_first, n_orders=agg.n_orders),
                churn_risk=churn_risk(recency, avg_gap),
                ltv=round(agg.total_spend, 2),
            )
        )
    return scores
