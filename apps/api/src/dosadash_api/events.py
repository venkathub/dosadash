"""Event cascade bus (Hard Rule 4): business-state mutations publish to Redis.

Channels (docs/06):
    pubsub:orders   — order.created / order.status  → KDS + tracking WS fan-out
    pubsub:menu     — (Phase 2) menu edits          → re-embed RAG, cache bust
    pubsub:settings — (Phase 2) kitchen pause etc.

Publishing is best-effort: a Redis outage must never fail a checkout.
"""

import json
import logging
from functools import lru_cache
from typing import Any

from redis.asyncio import Redis

from dosadash_api.config import get_settings
from dosadash_api.db.models import Order

logger = logging.getLogger(__name__)

ORDERS_CHANNEL = "pubsub:orders"


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def order_event_payload(event_type: str, order: Order) -> dict[str, Any]:
    """Serializable event body (unit-tested; consumed by KDS/tracking WS)."""
    return {
        "type": event_type,
        "order_id": order.id,
        "status": order.status.value,
        "user_id": order.user_id,
        "total": str(order.total),
        "channel": order.channel.value,
        "placed_at": order.placed_at.isoformat() if order.placed_at else None,
        "items": [
            {"name": oi.item.name if oi.item else str(oi.item_id), "qty": oi.qty}
            for oi in order.items
        ],
    }


async def publish_order_event(event_type: str, order: Order) -> None:
    """Fire-and-forget publish; logs (never raises) on Redis failure."""
    payload = order_event_payload(event_type, order)
    try:
        await get_redis().publish(ORDERS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning("order event publish failed (order %s)", order.id, exc_info=True)
