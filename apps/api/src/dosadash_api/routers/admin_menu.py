"""Admin menu ops (Phase 2): CRUD, 86 toggle, scheduling, customizations.

Every route requires admin/owner (RBAC per route), every mutation writes a
StaffAction audit row, and publishes to `pubsub:menu` (Hard Rule 4 — event
cascade) so the AI layer can re-embed RAG and bust caches in Phase 3.

Items referenced by past orders cannot be hard-deleted (FK) — 86 them instead.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import (
    Brand,
    Customization,
    Ingredient,
    MenuItem,
    MenuItemTranslation,
    OrderItem,
    RecipeIngredient,
    User,
)
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import (
    AvailabilityIn,
    CustomizationIn,
    CustomizationOut,
    MenuItemAdminOut,
    MenuItemCreateIn,
    MenuItemUpdateIn,
    RecipeIn,
    RecipeLineOut,
    Role,
    ScheduleIn,
)

router = APIRouter(prefix="/api/v1/admin/menu", tags=["admin:menu"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

_LOAD_ALL = (
    selectinload(MenuItem.recipe).selectinload(RecipeIngredient.ingredient),
    selectinload(MenuItem.customizations),
)


def _admin_out(item: MenuItem) -> MenuItemAdminOut:
    out = MenuItemAdminOut.model_validate(item)
    out.allergens = sorted(ri.ingredient.name for ri in item.recipe if ri.ingredient.is_allergen)
    out.ingredients = sorted(ri.ingredient.name for ri in item.recipe)
    return out


async def _get_item(session: AsyncSession, item_id: int) -> MenuItem:
    item = await session.scalar(select(MenuItem).where(MenuItem.id == item_id).options(*_LOAD_ALL))
    if item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return item


# ------------------------------------------------------------------ item CRUD


@router.get("/items", response_model=list[MenuItemAdminOut])
async def list_items(
    session: SessionDep,
    admin: User = AdminUser,
) -> list[MenuItemAdminOut]:
    """Full catalogue including 86'd items (unlike the public menu)."""
    items = (
        await session.scalars(
            select(MenuItem).options(*_LOAD_ALL).order_by(MenuItem.category, MenuItem.name)
        )
    ).all()
    return [_admin_out(i) for i in items]


@router.post("/items", response_model=MenuItemAdminOut, status_code=201)
async def create_item(
    body: MenuItemCreateIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> MenuItemAdminOut:
    brand_id = await session.scalar(select(Brand.id).order_by(Brand.id).limit(1))
    if brand_id is None:
        raise HTTPException(status_code=500, detail="No brand configured")
    item = MenuItem(brand_id=brand_id, **body.model_dump())
    session.add(item)
    audit.record(
        session, actor=admin, action="menu.create", entity="menu_item", detail={"name": body.name}
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Item with this name already exists") from exc
    await session.refresh(item, ["recipe", "customizations"])
    await events.publish_menu_event("menu.created", item_id=item.id, detail={"name": item.name})
    return _admin_out(item)


@router.patch("/items/{item_id}", response_model=MenuItemAdminOut)
async def update_item(
    item_id: int,
    body: MenuItemUpdateIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> MenuItemAdminOut:
    item = await _get_item(session, item_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in changes.items():
        setattr(item, field, value)
    # Localization staleness (Phase 7): if the canonical text changed, any
    # APPROVED translation of it is now unreviewed — pull it back to DRAFT
    # in the same transaction so stale Tamil never serves.
    stale_translations: list[str] = []
    if changes.keys() & {"name", "description", "category"}:
        rows = (
            await session.scalars(
                select(MenuItemTranslation).where(
                    MenuItemTranslation.item_id == item_id,
                    MenuItemTranslation.status == "APPROVED",
                )
            )
        ).all()
        for trans in rows:
            trans.status = "DRAFT"
            trans.reviewed_by = None
            stale_translations.append(trans.lang)
    audit.record(
        session,
        actor=admin,
        action="menu.update",
        entity=f"menu_item:{item.id}",
        detail={"fields": sorted(changes)}
        | ({"translations_reset": sorted(stale_translations)} if stale_translations else {}),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Item with this name already exists") from exc
    await events.publish_menu_event(
        "menu.updated", item_id=item.id, detail={"fields": sorted(changes)}
    )
    return _admin_out(item)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: int,
    session: SessionDep,
    admin: User = AdminUser,
) -> Response:
    item = await _get_item(session, item_id)
    referenced = await session.scalar(
        select(OrderItem.id).where(OrderItem.item_id == item_id).limit(1)
    )
    if referenced is not None:
        raise HTTPException(
            status_code=409,
            detail="Item is referenced by orders — mark it unavailable (86) instead",
        )
    name = item.name
    audit.record(
        session,
        actor=admin,
        action="menu.delete",
        entity=f"menu_item:{item_id}",
        detail={"name": name},
    )
    await session.delete(item)
    await session.commit()
    await events.publish_menu_event("menu.deleted", item_id=item_id, detail={"name": name})
    return Response(status_code=204)


# ------------------------------------------------------- availability / schedule


@router.post("/items/{item_id}/availability", response_model=MenuItemAdminOut)
async def set_availability(
    item_id: int,
    body: AvailabilityIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> MenuItemAdminOut:
    """The 86 toggle. Publishes menu.availability so agents stop offering it."""
    item = await _get_item(session, item_id)
    item.is_available = body.is_available
    audit.record(
        session,
        actor=admin,
        action="menu.86",
        entity=f"menu_item:{item.id}",
        detail={"name": item.name, "is_available": body.is_available},
    )
    await session.commit()
    await events.publish_menu_event(
        "menu.availability",
        item_id=item.id,
        detail={"name": item.name, "is_available": body.is_available},
    )
    return _admin_out(item)


@router.put("/items/{item_id}/schedule", response_model=MenuItemAdminOut)
async def set_schedule(
    item_id: int,
    body: ScheduleIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> MenuItemAdminOut:
    """Set per-weekday serving windows; null clears (always available)."""
    item = await _get_item(session, item_id)
    item.schedule = (
        {day: window.model_dump() for day, window in body.schedule.items()}
        if body.schedule is not None
        else None
    )
    audit.record(
        session,
        actor=admin,
        action="menu.schedule",
        entity=f"menu_item:{item.id}",
        detail={"schedule": item.schedule},
    )
    await session.commit()
    await events.publish_menu_event(
        "menu.schedule", item_id=item.id, detail={"schedule": item.schedule}
    )
    return _admin_out(item)


# ------------------------------------------------------------- recipe mapping


def _recipe_out(item: MenuItem) -> list[RecipeLineOut]:
    return sorted(
        (
            RecipeLineOut(
                ingredient_id=ri.ingredient_id,
                name=ri.ingredient.name,
                unit=ri.ingredient.unit,
                qty=ri.qty,
                is_allergen=ri.ingredient.is_allergen,
            )
            for ri in item.recipe
        ),
        key=lambda line: line.name,
    )


@router.get("/items/{item_id}/recipe", response_model=list[RecipeLineOut])
async def get_recipe(
    item_id: int, session: SessionDep, admin: User = AdminUser
) -> list[RecipeLineOut]:
    return _recipe_out(await _get_item(session, item_id))


@router.put("/items/{item_id}/recipe", response_model=list[RecipeLineOut])
async def set_recipe(
    item_id: int,
    body: RecipeIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> list[RecipeLineOut]:
    """Full-replace the recipe mapping. This is the single source of truth for
    allergen badges AND inventory depletion — hence the menu.recipe event so
    the AI layer re-embeds this dish's allergen facts."""
    item = await _get_item(session, item_id)
    ingredient_ids = [line.ingredient_id for line in body.lines]
    rows = (
        await session.scalars(select(Ingredient).where(Ingredient.id.in_(ingredient_ids)))
    ).all()
    by_id = {i.id: i for i in rows}
    missing = set(ingredient_ids) - set(by_id)
    if missing:
        raise HTTPException(status_code=404, detail=f"unknown ingredient ids: {sorted(missing)}")

    for old_line in list(item.recipe):
        await session.delete(old_line)
    await session.flush()
    session.add_all(
        RecipeIngredient(item_id=item.id, ingredient_id=line.ingredient_id, qty=line.qty)
        for line in body.lines
    )
    audit.record(
        session,
        actor=admin,
        action="menu.recipe",
        entity=f"menu_item:{item.id}",
        detail={"ingredient_ids": sorted(ingredient_ids)},
    )
    await session.commit()
    await session.refresh(item, ["recipe"])
    await events.publish_menu_event(
        "menu.recipe", item_id=item.id, detail={"ingredient_ids": sorted(ingredient_ids)}
    )
    return sorted(
        (
            RecipeLineOut(
                ingredient_id=line.ingredient_id,
                name=by_id[line.ingredient_id].name,
                unit=by_id[line.ingredient_id].unit,
                qty=line.qty,
                is_allergen=by_id[line.ingredient_id].is_allergen,
            )
            for line in body.lines
        ),
        key=lambda out: out.name,
    )


# ------------------------------------------------------------- customizations


@router.post("/items/{item_id}/customizations", response_model=CustomizationOut, status_code=201)
async def add_customization(
    item_id: int,
    body: CustomizationIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> CustomizationOut:
    item = await _get_item(session, item_id)
    cust = Customization(item_id=item.id, name=body.name, price_delta=body.price_delta)
    session.add(cust)
    audit.record(
        session,
        actor=admin,
        action="menu.customization.add",
        entity=f"menu_item:{item.id}",
        detail={"name": body.name, "price_delta": str(body.price_delta)},
    )
    await session.commit()
    await session.refresh(item, ["customizations"])  # keep loaded collection fresh
    await events.publish_menu_event(
        "menu.customization", item_id=item.id, detail={"added": body.name}
    )
    return CustomizationOut.model_validate(cust)


@router.delete("/customizations/{customization_id}", status_code=204)
async def delete_customization(
    customization_id: int,
    session: SessionDep,
    admin: User = AdminUser,
) -> Response:
    cust = await session.get(Customization, customization_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="Customization not found")
    item_id, name = cust.item_id, cust.name
    audit.record(
        session,
        actor=admin,
        action="menu.customization.delete",
        entity=f"menu_item:{item_id}",
        detail={"name": name},
    )
    await session.delete(cust)
    await session.commit()
    await events.publish_menu_event("menu.customization", item_id=item_id, detail={"removed": name})
    return Response(status_code=204)
