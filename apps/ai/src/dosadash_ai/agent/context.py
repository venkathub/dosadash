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
    spice_level: int
    is_available: bool
    schedule: dict[str, Any] | None
    description: str | None
    allergens: tuple[str, ...] = ()

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
class AgentContext:
    items: dict[int, MenuItemCtx] = field(default_factory=dict)
    kitchen_paused: bool = False
    business_hours: dict[str, Any] | None = None
    prefs: UserPrefs | None = None

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

    menu_rows = await session.execute(
        text(
            "SELECT id, name, category, price, is_veg, spice_level, is_available, "
            "schedule, description FROM menu_items ORDER BY category, name"
        )
    )
    items = {
        row.id: MenuItemCtx(
            id=row.id,
            name=row.name,
            category=row.category,
            price=Decimal(row.price),
            is_veg=row.is_veg,
            spice_level=row.spice_level,
            is_available=row.is_available,
            schedule=row.schedule,
            description=row.description,
            allergens=tuple(sorted(allergens_by_item.get(row.id, []))),
        )
        for row in menu_rows
    }

    settings_row = (
        await session.execute(
            text("SELECT kitchen_paused, business_hours FROM settings WHERE id = 1")
        )
    ).first()

    prefs = None
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
    )


def menu_payload(ctx: AgentContext) -> list[dict[str, Any]]:
    """Compact menu JSON for the prompt. Sold-out / off-schedule items are
    included but flagged, so the agent can say "sold out" instead of
    pretending the dish doesn't exist."""
    return [
        {
            "item_id": item.id,
            "name": item.name,
            "category": item.category,
            "price_inr": str(item.price),
            "veg": item.is_veg,
            "spice": item.spice_level,
            "allergens": list(item.allergens),
            "available": item.orderable,
        }
        for item in ctx.items.values()
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
