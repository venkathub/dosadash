"""Event cascade subscriber (Hard Rule 4): the AI layer follows business state.

Subscribes to the Redis channels the core API publishes on (docs/06):

    pubsub:menu — menu.create/update → re-embed `menu_items.embedding`
                  (semantic menu search / recsys cold-start); catalog.* and
                  menu.delete/availability need no embedding work yet but are
                  logged for observability.

Knowledge markdown re-embedding happens on service startup via the
hash-diffed ingester (knowledge/ ships with the deploy, so an edit implies
a restart); menu rows mutate at runtime, hence the live subscription.

Everything here is best-effort: a Redis outage degrades freshness, never
availability. The listener reconnects with backoff forever.
"""

import asyncio
import json
import logging
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.client import embed_texts
from dosadash_ai.llm.semcache import get_semcache

logger = logging.getLogger(__name__)

MENU_CHANNEL = "pubsub:menu"  # must match dosadash_api.events
_RECONNECT_SECONDS = 5.0

# Events that change text the embedding is built from.
_REEMBED_EVENTS = {"menu.created", "menu.updated"}


async def reembed_menu_item(session: AsyncSession, item_id: int) -> bool:
    """Refresh menu_items.embedding from the row's current text. Returns
    False when the row no longer exists (deleted between event and handling)."""
    row = (
        await session.execute(
            text("SELECT name, category, description FROM menu_items WHERE id = :id"),
            {"id": item_id},
        )
    ).first()
    if row is None:
        return False
    content = f"{row.name} — {row.category}. {row.description or ''}".strip()
    [embedding] = await embed_texts([content], trace_name="cascade.menu.embed")
    await session.execute(
        text("UPDATE menu_items SET embedding = :embedding WHERE id = :id"),
        {"embedding": json.dumps(embedding), "id": item_id},
    )
    await session.commit()
    return True


async def handle_menu_event(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Route one pubsub:menu event. Unknown/irrelevant events are logged only.

    EVERY menu event flushes the semantic cache: cached Q&A may cite menu
    facts (allergen guide is generated from the menu), so any mutation —
    availability, price, delete — must invalidate (Hard Rule 4)."""
    await get_semcache().flush()
    event_type = payload.get("type", "")
    item_id = payload.get("item_id")
    if event_type in _REEMBED_EVENTS and isinstance(item_id, int):
        found = await reembed_menu_item(session, item_id)
        logger.info(
            "cascade: %s item=%s → %s", event_type, item_id, "re-embedded" if found else "gone"
        )
    else:
        logger.debug("cascade: ignoring event %s", event_type)


async def run_menu_listener() -> None:
    """Forever-loop: subscribe, handle, reconnect on any failure."""
    while True:
        redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe(MENU_CHANNEL)
                logger.info("cascade: subscribed to %s", MENU_CHANNEL)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        async with get_sessionmaker()() as session:
                            await handle_menu_event(session, payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — one bad event ≠ dead listener
                        logger.warning("cascade: event handling failed", exc_info=True)
        except asyncio.CancelledError:
            await redis.aclose()
            raise
        except Exception:  # noqa: BLE001 — reconnect forever, best-effort by design
            logger.warning("cascade: redis connection lost, retrying in %ss", _RECONNECT_SECONDS)
            await asyncio.sleep(_RECONNECT_SECONDS)
        finally:
            await redis.aclose()
