"""Business-state context for the order agent — read fresh from the DB every
turn, so the agent can never drift from menu edits, 86'ing, or a kitchen
pause (Hard Rule 4: the event cascade keeps embeddings fresh; the agent
additionally re-reads authoritative state per turn).

Raw SQL on purpose: the AI service reads business tables but never owns
them (models live in apps/api; mutations go through its services).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_shared import availability


@dataclass(frozen=True)
class MenuItemCtx:
    id: int
    name: str
    category: str
    price: Decimal
    is_veg: bool
    contains_onion_garlic: bool
    spice_level: int
    is_available: bool
    schedule: dict[str, Any] | None
    description: str | None
    allergens: tuple[str, ...] = ()
    meal_periods: tuple[str, ...] = ()
    # Owner-APPROVED translated names (Phase 7) — lets a Tamil "மசாலா தோசை"
    # map to the canonical item. Drafts never reach the agent.
    aliases: tuple[str, ...] = ()

    @property
    def orderable(self) -> bool:
        return self.is_available and availability.item_on_schedule(self.schedule)


@dataclass(frozen=True)
class UserPrefs:
    diet: str | None = None
    allergens: tuple[str, ...] = ()
    spice_level: int | None = None
    language: str = "en"


@dataclass(frozen=True)
class UserMemoryCtx:
    """Long-term memory (Phase 6): derived "usual" + recent order episodes."""

    usual: dict[str, Any] | None = None  # {"items": [{item_id, name, qty}], "times_ordered": n}
    recent_orders: tuple[str, ...] = ()  # newest first, from user_memories EPISODE rows


@dataclass(frozen=True)
class AgentContext:
    items: dict[int, MenuItemCtx] = field(default_factory=dict)
    kitchen_paused: bool = False
    business_hours: dict[str, Any] | None = None
    prefs: UserPrefs | None = None
    memory: UserMemoryCtx | None = None

    @property
    def kitchen_open(self) -> bool:
        return not self.kitchen_paused and availability.is_open(self.business_hours)


async def load_context(session: AsyncSession, user_id: int | None) -> AgentContext:
    """One snapshot of menu + settings + preferences for a single turn."""
    allergen_rows = await session.execute(
        text(
            "SELECT ri.item_id, i.name FROM recipe_ingredients ri "
            "JOIN ingredients i ON i.id = ri.ingredient_id WHERE i.is_allergen"
        )
    )
    allergens_by_item: dict[int, list[str]] = {}
    for item_id, name in allergen_rows:
        allergens_by_item.setdefault(item_id, []).append(name)

    alias_rows = await session.execute(
        text(
            "SELECT item_id, name FROM menu_item_translations "
            "WHERE status = 'APPROVED' ORDER BY lang"
        )
    )
    aliases_by_item: dict[int, list[str]] = {}
    for item_id, name in alias_rows:
        aliases_by_item.setdefault(item_id, []).append(name)

    menu_rows = await session.execute(
        text(
            "SELECT id, name, category, price, is_veg, contains_onion_garlic, spice_level, "
            "is_available, schedule, description, meal_periods "
            "FROM menu_items ORDER BY category, name"
        )
    )
    items = {
        row.id: MenuItemCtx(
            id=row.id,
            name=row.name,
            category=row.category,
            price=Decimal(row.price),
            is_veg=row.is_veg,
            contains_onion_garlic=row.contains_onion_garlic,
            spice_level=row.spice_level,
            is_available=row.is_available,
            schedule=row.schedule,
            description=row.description,
            allergens=tuple(sorted(allergens_by_item.get(row.id, []))),
            meal_periods=tuple(row.meal_periods or ()),
            aliases=tuple(aliases_by_item.get(row.id, [])),
        )
        for row in menu_rows
    }

    settings_row = (
        await session.execute(
            text("SELECT kitchen_paused, business_hours FROM settings WHERE id = 1")
        )
    ).first()

    prefs = None
    memory = None
    if user_id is not None:
        memory = await load_memory(session, user_id)
    if user_id is not None:
        prefs_row = (
            await session.execute(
                text(
                    "SELECT diet, allergens, spice_level, language "
                    "FROM user_preferences WHERE user_id = :uid"
                ),
                {"uid": user_id},
            )
        ).first()
        if prefs_row is not None:
            prefs = UserPrefs(
                diet=prefs_row.diet,
                allergens=tuple(prefs_row.allergens or ()),
                spice_level=prefs_row.spice_level,
                language=prefs_row.language or "en",
            )

    return AgentContext(
        items=items,
        kitchen_paused=bool(settings_row.kitchen_paused) if settings_row else False,
        business_hours=settings_row.business_hours if settings_row else None,
        prefs=prefs,
        memory=memory,
    )


USUAL_WINDOW_DAYS = 90
USUAL_MIN_REPEATS = 2  # an order signature seen once is history, not a habit
MAX_EPISODES = 3

_USUAL_SQL = text(
    """
    SELECT o.id, oi.item_id, oi.qty, m.name
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN menu_items m ON m.id = oi.item_id
    WHERE o.user_id = :uid AND o.status != 'CANCELLED'
      AND o.placed_at >= now() - make_interval(days => :days)
    ORDER BY o.placed_at DESC
    """
)

_EPISODES_SQL = text(
    """
    SELECT content FROM user_memories
    WHERE user_id = :uid AND kind = 'EPISODE'
    ORDER BY at DESC, id DESC LIMIT :limit
    """
)


async def load_memory(session: AsyncSession, user_id: int) -> UserMemoryCtx:
    """Derive "my usual" (most-repeated exact order signature over the
    window, ≥ USUAL_MIN_REPEATS) + latest episode summaries."""
    rows = (
        await session.execute(_USUAL_SQL, {"uid": user_id, "days": USUAL_WINDOW_DAYS})
    ).fetchall()
    orders: dict[int, list[tuple[int, int, str]]] = {}
    for row in rows:
        orders.setdefault(row.id, []).append((row.item_id, row.qty, row.name))

    signatures: dict[tuple[tuple[int, int], ...], list[list[tuple[int, int, str]]]] = {}
    for lines in orders.values():
        signature = tuple(sorted((item_id, qty) for item_id, qty, _ in lines))
        signatures.setdefault(signature, []).append(lines)

    usual = None
    if signatures:
        best_lines = max(signatures.values(), key=len)
        if len(best_lines) >= USUAL_MIN_REPEATS:
            usual = {
                "items": [
                    {"item_id": item_id, "name": name, "qty": qty}
                    for item_id, qty, name in sorted(best_lines[0])
                ],
                "times_ordered": len(best_lines),
            }

    episodes = (
        await session.execute(_EPISODES_SQL, {"uid": user_id, "limit": MAX_EPISODES})
    ).scalars()
    return UserMemoryCtx(usual=usual, recent_orders=tuple(episodes))


def menu_payload(ctx: AgentContext) -> list[dict[str, Any]]:
    """Compact menu JSON for the prompt — ORDERABLE dishes only (Phase 11).

    Presence = orderability. Every live-gate experiment that exposed
    off-window dishes or serving-hours text to the model (flagged entries,
    a separate not_serving_now list, timing text in knowledge) made
    gpt-4o-mini hallucinate refusals of dishes that WERE on the menu. So
    the model sees a clean "this is what we serve right now" world, and
    the serving-window story is computed deterministically in
    `guardrail.serving_notes` (dish-QC philosophy: the model observes,
    the verdict is computed).

    `aliases` (approved translated names, Phase 7) appears ONLY when an item
    has any — a menu with no approved translations serializes byte-identically
    to the pre-localization payload, so prompt prefix caching and the live
    eval gate are unaffected until translations are actually approved."""
    return [
        {
            "item_id": item.id,
            "name": item.name,
            "category": item.category,
            "price_inr": str(item.price),
            "veg": item.is_veg,
            "jain_friendly": item.is_veg and not item.contains_onion_garlic,
            "spice": item.spice_level,
            "allergens": list(item.allergens),
            # meal_periods deliberately NOT serialized since v5: the hard
            # serving windows already keep the orderable menu time-
            # appropriate, and the model misread any meal-list field as an
            # availability schedule
            "available": True,
            **({"aliases": list(item.aliases)} if item.aliases else {}),
        }
        for item in ctx.items.values()
        if item.orderable
    ]


def prefs_payload(ctx: AgentContext) -> dict[str, Any] | None:
    if ctx.prefs is None:
        return None
    return {
        "diet": ctx.prefs.diet,
        "allergens": list(ctx.prefs.allergens),
        "preferred_spice": ctx.prefs.spice_level,
        "language": ctx.prefs.language,
    }


def memory_payload(ctx: AgentContext) -> dict[str, Any] | None:
    """Long-term memory for the STATE message (volatile section — sits after
    the cache-stable prefix, so per-user data never breaks prefix caching)."""
    if ctx.memory is None:
        return None
    return {"usual": ctx.memory.usual, "recent_orders": list(ctx.memory.recent_orders)}
