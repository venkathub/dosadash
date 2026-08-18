"""Long-term agent memory (Phase 6): episodic store writes.

One EPISODE row per placed order ("2× Masala Dosa, 1× Filter Coffee
(₹315.00) — 2026-08-18"). The ai context loader reads these (and derives
"my usual" from order history) so the order agent can honour "my usual" /
"same as last time" for logged-in customers.

Queued on the caller's session (commits atomically with the order); a
memory row must never fail a checkout, so callers add it pre-commit and
any failure surfaces as the order's own failure semantics.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import MenuItem, Order, UserMemory


def record_order_episode(
    session: AsyncSession, *, order: Order, found: dict[int, MenuItem]
) -> None:
    """Queue an EPISODE memory for a just-built order (pre-commit)."""
    summary = ", ".join(f"{oi.qty}× {found[oi.item.id].name}" for oi in order.items)
    day = datetime.now(UTC).date().isoformat()
    session.add(
        UserMemory(
            user_id=order.user_id,
            kind="EPISODE",
            content=f"{summary} (₹{order.total}) — {day}",
            meta={
                "items": [{"item_id": oi.item.id, "qty": oi.qty} for oi in order.items],
                "total": str(order.total),
                "channel": order.channel.value,
            },
        )
    )
