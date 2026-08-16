"""Event cascade bus (Hard Rule 4): business-state mutations publish to Redis.

Channels (docs/06):
    pubsub:orders   — order.created / order.status  → KDS + tracking WS fan-out
    pubsub:menu     — menu.* (create/update/delete/availability/schedule/
                      customization) → Phase 3 re-embeds RAG, busts caches
    pubsub:settings — settings.updated / settings.kitchen_pause → agent
                      behavior (stop taking orders while paused), cache bust

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
MENU_CHANNEL = "pubsub:menu"
SETTINGS_CHANNEL = "pubsub:settings"


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


def menu_event_payload(
    event_type: str, *, item_id: int, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Serializable menu-mutation event (consumed by the AI layer in Phase 3)."""
    return {"type": event_type, "item_id": item_id, "detail": detail or {}}


async def publish_menu_event(
    event_type: str, *, item_id: int, detail: dict[str, Any] | None = None
) -> None:
    """Fire-and-forget publish; logs (never raises) on Redis failure."""
    payload = menu_event_payload(event_type, item_id=item_id, detail=detail)
    try:
        await get_redis().publish(MENU_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning("menu event publish failed (item %s)", item_id, exc_info=True)


def settings_event_payload(
    event_type: str, *, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Serializable settings-mutation event (single-row settings — no entity id)."""
    return {"type": event_type, "detail": detail or {}}


async def publish_settings_event(event_type: str, *, detail: dict[str, Any] | None = None) -> None:
    """Fire-and-forget publish; logs (never raises) on Redis failure."""
    payload = settings_event_payload(event_type, detail=detail)
    try:
        await get_redis().publish(SETTINGS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning("settings event publish failed (%s)", event_type, exc_info=True)
