"""Public menu endpoints (read-only).

GET /api/v1/menu             — available items, filterable
GET /api/v1/menu/categories  — categories with item counts
GET /api/v1/menu/items/{id}  — full detail incl. ingredients/allergens
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.db.models import Ingredient, MenuItem, RecipeIngredient
from dosadash_api.db.session import get_session
from dosadash_shared import CategoryOut, MenuItemDetail, MenuItemSummary

router = APIRouter(prefix="/api/v1/menu", tags=["menu"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_LOAD_RECIPE = selectinload(MenuItem.recipe).selectinload(RecipeIngredient.ingredient)


def _allergens(item: MenuItem) -> list[str]:
    return sorted(ri.ingredient.name for ri in item.recipe if ri.ingredient.is_allergen)


def _summary(item: MenuItem) -> MenuItemSummary:
    out = MenuItemSummary.model_validate(item)
    out.allergens = _allergens(item)
    return out


@router.get("", response_model=list[MenuItemSummary])
async def list_menu(
    session: SessionDep,
    category: str | None = None,
    veg: bool | None = None,
    max_spice: Annotated[int | None, Query(ge=0, le=3)] = None,
    exclude_allergens: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
) -> list[MenuItemSummary]:
    stmt = select(MenuItem).where(MenuItem.is_available).options(_LOAD_RECIPE)
    if category:
        stmt = stmt.where(func.lower(MenuItem.category) == category.lower())
    if veg is not None:
        stmt = stmt.where(MenuItem.is_veg == veg)
    if max_spice is not None:
        stmt = stmt.where(MenuItem.spice_level <= max_spice)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(MenuItem.name.ilike(pattern) | MenuItem.description.ilike(pattern))
    if exclude_allergens:
        lowered = [a.lower() for a in exclude_allergens]
        contains_allergen = (
            select(RecipeIngredient.item_id)
            .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
            .where(
                RecipeIngredient.item_id == MenuItem.id,
                Ingredient.is_allergen,
                func.lower(Ingredient.name).in_(lowered),
            )
        )
        stmt = stmt.where(~exists(contains_allergen))
    stmt = stmt.order_by(MenuItem.category, MenuItem.name)
    items = (await session.scalars(stmt)).all()
    return [_summary(i) for i in items]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: SessionDep) -> list[CategoryOut]:
    rows = await session.execute(
        select(MenuItem.category, func.count())
        .where(MenuItem.is_available)
        .group_by(MenuItem.category)
        .order_by(MenuItem.category)
    )
    return [CategoryOut(name=name, item_count=count) for name, count in rows.all()]


@router.get("/items/{item_id}", response_model=MenuItemDetail)
async def get_item(item_id: int, session: SessionDep) -> MenuItemDetail:
    stmt = (
        select(MenuItem)
        .where(MenuItem.id == item_id)
        .options(_LOAD_RECIPE, selectinload(MenuItem.customizations))
    )
    item = await session.scalar(stmt)
    if item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    out = MenuItemDetail.model_validate(item)
    out.allergens = _allergens(item)
    out.ingredients = sorted(ri.ingredient.name for ri in item.recipe)
    return out
