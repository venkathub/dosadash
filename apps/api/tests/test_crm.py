"""CRM scoring tests — pure functions, no DB."""

from datetime import UTC, datetime, timedelta

from dosadash_api.worker.crm import UserAggregate, churn_risk, score_segments

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def _agg(user_id, *, orders, spend, first_days_ago, last_days_ago):
    return UserAggregate(
        user_id=user_id,
        n_orders=orders,
        total_spend=spend,
        first_order_at=NOW - timedelta(days=first_days_ago),
        last_order_at=NOW - timedelta(days=last_days_ago),
    )


def _base():
    """A spread of personas so quintiles are meaningful."""
    aggs = []
    uid = 0
    for last, orders, spend, first in [
        (1, 60, 30000, 350),  # weekly regular, ordered yesterday
        (3, 48, 25000, 340),
        (5, 30, 15000, 330),
        (10, 20, 9000, 300),
        (20, 12, 5000, 300),
        (35, 8, 3500, 280),
        (60, 6, 2500, 300),
        (90, 4, 1500, 250),
        (150, 2, 700, 200),
        (250, 1, 300, 250),
    ]:
        uid += 1
        aggs.append(_agg(uid, orders=orders, spend=spend, first_days_ago=first, last_days_ago=last))
    return aggs


def test_churn_risk_monotonic_and_bounded():
    risks = [churn_risk(days, avg_gap_days=7.0) for days in (0, 3, 7, 14, 30, 90)]
    assert risks == sorted(risks)
    assert risks[0] == 0.0
    assert all(0.0 <= r <= 1.0 for r in risks)
    # ~2 weeks of silence for a weekly customer → meaningful risk
    assert risks[3] > 0.6


def test_churn_uses_personal_rhythm():
    # 30 days silent: alarming for a weekly customer, normal for a monthly one
    weekly = churn_risk(30, avg_gap_days=7.0)
    monthly = churn_risk(30, avg_gap_days=30.0)
    assert weekly > monthly


def test_tiers_cover_the_spectrum():
    scores = {s.user_id: s for s in score_segments(_base(), now=NOW)}
    assert scores[1].rfm_tier == "CHAMPION"  # frequent + fresh
    assert scores[10].rfm_tier == "LOST"  # one order, 250 days ago
    tiers = {s.rfm_tier for s in scores.values()}
    assert {"CHAMPION", "LOST"} <= tiers
    assert len(tiers) >= 4  # a real segmentation, not one bucket


def test_new_user_tier_and_ltv():
    base = _base() + [_agg(99, orders=1, spend=450, first_days_ago=5, last_days_ago=5)]
    scores = {s.user_id: s for s in score_segments(base, now=NOW)}
    assert scores[99].rfm_tier == "NEW"
    assert scores[99].ltv == 450.0
    assert scores[99].churn_risk < 0.3  # just arrived — not churning


def test_empty_base():
    assert score_segments([], now=NOW) == []
