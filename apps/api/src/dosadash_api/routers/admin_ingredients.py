"""Admin ingredient catalogue (Phase 2): CRUD backing the recipe mapping.

Ingredients drive two things (docs/06): inventory (Phase 6 agent) and the
allergen knowledge base (RAG). Flipping `is_allergen` changes the allergen
badges of every dish using it — so mutations publish ingredient.* events on
the menu channel for downstream re-embedding (Hard Rule 4).

Deleting an ingredient referenced by a recipe is refused (409): the FK would
silently cascade recipe lines away and corrupt the allergen KB.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Ingredient, RecipeIngredient, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import IngredientIn, IngredientOut, IngredientUpdateIn, Role

router = APIRouter(prefix="/api/v1/admin/ingredients", tags=["admin:ingredients"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


async def _get_ingredient(session: AsyncSession, ingredient_id: int) -> Ingredient:
    ingredient = await session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    return ingredient


@router.get("", response_model=list[IngredientOut])
async def list_ingredients(session: SessionDep, admin: User = AdminUser) -> list[IngredientOut]:
    rows = (await session.scalars(select(Ingredient).order_by(Ingredient.name))).all()
    return [IngredientOut.model_validate(i) for i in rows]


@router.post("", response_model=IngredientOut, status_code=201)
async def create_ingredient(
    body: IngredientIn, session: SessionDep, admin: User = AdminUser
) -> IngredientOut:
    ingredient = Ingredient(**body.model_dump())
    session.add(ingredient)
    audit.record(
        session,
        actor=admin,
        action="ingredient.create",
        entity="ingredient",
        detail={"name": body.name},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ingredient name already exists") from exc
    await events.publish_catalog_event(
        "ingredient.created", detail={"ingredient_id": ingredient.id, "name": ingredient.name}
    )
    return IngredientOut.model_validate(ingredient)


@router.patch("/{ingredient_id}", response_model=IngredientOut)
async def update_ingredient(
    ingredient_id: int, body: IngredientUpdateIn, session: SessionDep, admin: User = AdminUser
) -> IngredientOut:
    ingredient = await _get_ingredient(session, ingredient_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in changes.items():
        setattr(ingredient, field, value)
    audit.record(
        session,
        actor=admin,
        action="ingredient.update",
        entity=f"ingredient:{ingredient.id}",
        detail={"fields": sorted(changes)},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Ingredient name already exists") from exc
    await events.publish_catalog_event(
        "ingredient.updated",
        detail={"ingredient_id": ingredient.id, "fields": sorted(changes)},
    )
    return IngredientOut.model_validate(ingredient)


@router.delete("/{ingredient_id}", status_code=204)
async def delete_ingredient(
    ingredient_id: int, session: SessionDep, admin: User = AdminUser
) -> None:
    ingredient = await _get_ingredient(session, ingredient_id)
    used_by = await session.scalar(
        select(RecipeIngredient.item_id)
        .where(RecipeIngredient.ingredient_id == ingredient_id)
        .limit(1)
    )
    if used_by is not None:
        raise HTTPException(
            status_code=409,
            detail="Ingredient is used in recipes — remove it from recipes first",
        )
    name = ingredient.name
    audit.record(
        session,
        actor=admin,
        action="ingredient.delete",
        entity=f"ingredient:{ingredient_id}",
        detail={"name": name},
    )
    await session.delete(ingredient)
    await session.commit()
    await events.publish_catalog_event(
        "ingredient.deleted", detail={"ingredient_id": ingredient_id, "name": name}
    )
