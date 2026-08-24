"""Public menu endpoints (read-only).

GET /api/v1/menu             — available items, filterable
GET /api/v1/menu/categories  — categories with item counts
GET /api/v1/menu/combos     — APPROVED combos only
GET /api/v1/menu/items/{id}  — full detail incl. ingredients/allergens

Localization (Phase 7): `?lang=ta` overlays owner-APPROVED translations
per field (name/description/category label) with canonical fallback —
drafts never serve, prices/allergens/flags always come from the canonical
row, and `category` stays the canonical key.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.db.models import (
    Combo,
    Ingredient,
    MenuItem,
    MenuItemTranslation,
    NutritionEstimateRecord,
    RecipeIngredient,
)
from dosadash_api.db.session import get_session
from dosadash_shared import (
    SUPPORTED_TRANSLATION_LANGS,
    CategoryOut,
    ComboOut,
    MenuItemDetail,
    MenuItemSummary,
    availability,
)

router = APIRouter(prefix="/api/v1/menu", tags=["menu"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_LOAD_RECIPE = selectinload(MenuItem.recipe).selectinload(RecipeIngredient.ingredient)


def _check_lang(lang: str | None) -> None:
    if lang is not None and lang not in SUPPORTED_TRANSLATION_LANGS:
        raise HTTPException(status_code=422, detail=f"Unsupported language {lang!r}")


async def _approved_translations(
    session: AsyncSession, lang: str | None
) -> dict[int, MenuItemTranslation]:
    """Owner-APPROVED rows only — drafts/rejected never leave the backoffice."""
    if lang is None:
        return {}
    rows = (
        await session.scalars(
            select(MenuItemTranslation).where(
                MenuItemTranslation.lang == lang, MenuItemTranslation.status == "APPROVED"
            )
        )
    ).all()
    return {t.item_id: t for t in rows}


def _localize(out: MenuItemSummary, item: MenuItem, trans: MenuItemTranslation | None) -> None:
    """Per-field overlay with canonical fallback (in place)."""
    if trans is None:
        return
    out.canonical_name = item.name
    out.name = trans.name
    out.description = trans.description or item.description
    out.category_label = trans.category_label


def _allergens(item: MenuItem) -> list[str]:
    return sorted(ri.ingredient.name for ri in item.recipe if ri.ingredient.is_allergen)


def _summary(item: MenuItem, trans: MenuItemTranslation | None = None) -> MenuItemSummary:
    out = MenuItemSummary.model_validate(item)
    out.allergens = _allergens(item)
    out.available_now = availability.item_on_schedule(item.schedule)
    out.serving_windows = availability.serving_windows_text(item.schedule)
    _localize(out, item, trans)
    return out


@router.get("", response_model=list[MenuItemSummary])
async def list_menu(
    session: SessionDep,
    category: str | None = None,
    veg: bool | None = None,
    max_spice: Annotated[int | None, Query(ge=0, le=3)] = None,
    exclude_allergens: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=80)] = None,
    lang: str | None = None,
) -> list[MenuItemSummary]:
    _check_lang(lang)
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
    translations = await _approved_translations(session, lang)
    # Off-window dishes stay visible but annotated (available_now=False +
    # serving_windows text) — checkout and the agent still hard-block them.
    return [_summary(i, translations.get(i.id)) for i in items]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: SessionDep, lang: str | None = None) -> list[CategoryOut]:
    _check_lang(lang)
    rows = await session.execute(
        select(MenuItem.category, func.count())
        .where(MenuItem.is_available)
        .group_by(MenuItem.category)
        .order_by(MenuItem.category)
    )
    labels: dict[str, str] = {}
    if lang is not None:
        label_rows = await session.execute(
            select(MenuItem.category, MenuItemTranslation.category_label)
            .join(MenuItemTranslation, MenuItemTranslation.item_id == MenuItem.id)
            .where(
                MenuItemTranslation.lang == lang,
                MenuItemTranslation.status == "APPROVED",
                MenuItemTranslation.category_label.is_not(None),
            )
        )
        for category, label in label_rows:
            labels.setdefault(category, label)
    return [
        CategoryOut(name=name, item_count=count, label=labels.get(name))
        for name, count in rows.all()
    ]


@router.get("/combos", response_model=list[ComboOut])
async def list_combos(session: SessionDep) -> list[ComboOut]:
    """Owner-approved combos only (drafts/rejected stay in the backoffice)."""
    rows = await session.scalars(
        select(Combo).where(Combo.status == "APPROVED").order_by(Combo.id.desc())
    )
    return [ComboOut.model_validate(c) for c in rows]


@router.get("/items/{item_id}", response_model=MenuItemDetail)
async def get_item(item_id: int, session: SessionDep, lang: str | None = None) -> MenuItemDetail:
    _check_lang(lang)
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
    out.available_now = availability.item_on_schedule(item.schedule)
    out.serving_windows = availability.serving_windows_text(item.schedule)
    out.ingredients = sorted(ri.ingredient.name for ri in item.recipe)
    if lang is not None:
        trans = await session.get(MenuItemTranslation, (item_id, lang))
        if trans is not None and trans.status == "APPROVED":
            _localize(out, item, trans)
    nutrition = await session.get(NutritionEstimateRecord, item_id)
    if nutrition is not None and nutrition.status == "APPROVED":
        out.nutrition = nutrition.estimate  # owner-verified only (never drafts)
    return out
