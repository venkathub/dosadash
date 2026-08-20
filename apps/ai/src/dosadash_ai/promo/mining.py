"""Deterministic candidate mining for promo suggestions (Phase 7).

The DB decides WHAT could be promoted; the LLM only decides what to call
it. Raw SQL on business tables (read-only, same convention as
agent/context.py): co-ordered item pairs not already covered by a combo,
plus coupon context (slowest weekday by revenue, median order value,
existing codes for dedupe).
"""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_shared import MinedPair, PromoStats

WINDOW_DAYS = 90
MAX_PAIRS = 6
MIN_PAIR_ORDERS = 3  # a pair seen twice is coincidence, not a combo

_PAIRS_SQL = text(
    """
    SELECT a.item_id AS id_a, b.item_id AS id_b, COUNT(*) AS times
    FROM order_items a
    JOIN order_items b ON b.order_id = a.order_id AND a.item_id < b.item_id
    JOIN orders o ON o.id = a.order_id
    WHERE o.status != 'CANCELLED'
      AND o.placed_at >= now() - make_interval(days => :days)
    GROUP BY a.item_id, b.item_id
    HAVING COUNT(*) >= :min_orders
    ORDER BY times DESC
    LIMIT :limit
    """
)

_SLOW_DAY_SQL = text(
    """
    SELECT TRIM(TO_CHAR(o.placed_at AT TIME ZONE 'Asia/Kolkata', 'Day')) AS dow,
           COALESCE(SUM(o.total), 0) AS revenue
    FROM orders o
    WHERE o.status != 'CANCELLED'
      AND o.placed_at >= now() - make_interval(days => :days)
    GROUP BY dow ORDER BY revenue ASC LIMIT 1
    """
)

_MEDIAN_AOV_SQL = text(
    """
    SELECT COALESCE(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.total), 0)
    FROM orders o
    WHERE o.status != 'CANCELLED'
      AND o.placed_at >= now() - make_interval(days => :days)
    """
)


async def mine_pairs(session: AsyncSession) -> list[MinedPair]:
    """Top co-ordered pairs of currently-orderable items, minus pairs an
    existing combo (any status) already covers — never re-suggest."""
    covered: set[tuple[int, ...]] = set()
    for row in await session.execute(text("SELECT item_ids FROM combos")):
        covered.add(tuple(sorted(row.item_ids)))

    menu = {
        row.id: (row.name, Decimal(row.price))
        for row in await session.execute(
            text("SELECT id, name, price FROM menu_items WHERE is_available")
        )
    }

    pairs: list[MinedPair] = []
    rows = await session.execute(
        _PAIRS_SQL, {"days": WINDOW_DAYS, "min_orders": MIN_PAIR_ORDERS, "limit": MAX_PAIRS * 3}
    )
    for row in rows:
        key = (row.id_a, row.id_b)
        if key in covered or row.id_a not in menu or row.id_b not in menu:
            continue
        name_a, price_a = menu[row.id_a]
        name_b, price_b = menu[row.id_b]
        pairs.append(
            MinedPair(
                item_ids=[row.id_a, row.id_b],
                names=[name_a, name_b],
                parts_total=price_a + price_b,
                times_ordered=row.times,
            )
        )
        if len(pairs) >= MAX_PAIRS:
            break
    return pairs


async def gather_stats(session: AsyncSession) -> PromoStats:
    slow = (await session.execute(_SLOW_DAY_SQL, {"days": WINDOW_DAYS})).first()
    median = await session.scalar(_MEDIAN_AOV_SQL, {"days": WINDOW_DAYS})
    codes = [
        row.code for row in await session.execute(text("SELECT code FROM coupons ORDER BY id"))
    ]
    return PromoStats(
        slow_day=(slow.dow if slow else "Tuesday"),
        median_aov=Decimal(median or 0).quantize(Decimal("0.01")),
        existing_codes=codes,
    )
