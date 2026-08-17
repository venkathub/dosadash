"""Admin nutrition enrichment (Phase 2): LLM batch drafts, owner-verified.

POST /api/v1/admin/nutrition/enrich        — draft estimates for ≤10 items
GET  /api/v1/admin/nutrition               — review queue (filter by status)
POST /api/v1/admin/nutrition/{id}/status   — approve/reject (human gate)

The LLM never publishes anything directly: estimates land as DRAFT and only
an explicit approval exposes them on the public menu. Recipe mapping is the
input (single source of truth); items without a recipe are refused.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import MenuItem, NutritionEstimateRecord, RecipeIngredient, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    NutritionEnrichFailure,
    NutritionEnrichIn,
    NutritionEnrichOut,
    NutritionEstimateRequest,
    NutritionOut,
    NutritionStatusIn,
    RecipeContextLine,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/nutrition", tags=["admin:nutrition"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AIClientDep = Annotated[AIClient, Depends(get_ai_client)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

_LOAD_RECIPE = selectinload(MenuItem.recipe).selectinload(RecipeIngredient.ingredient)


@router.post("/enrich", response_model=NutritionEnrichOut)
async def enrich(
    body: NutritionEnrichIn,
    session: SessionDep,
    ai: AIClientDep,
    admin: User = AdminUser,
) -> NutritionEnrichOut:
    """Draft (or re-draft) estimates. Per-item failures don't sink the batch."""
    items = (
        await session.scalars(
            select(MenuItem).where(MenuItem.id.in_(set(body.item_ids))).options(_LOAD_RECIPE)
        )
    ).all()
    found = {m.id: m for m in items}

    out = NutritionEnrichOut()
    for item_id in body.item_ids:
        item = found.get(item_id)
        if item is None:
            out.failed.append(NutritionEnrichFailure(item_id=item_id, error="unknown item id"))
            continue
        if not item.recipe:
            out.failed.append(
                NutritionEnrichFailure(item_id=item_id, error="no recipe mapping — map it first")
            )
            continue
        request = NutritionEstimateRequest(
            item_name=item.name,
            category=item.category,
            description=item.description,
            is_veg=item.is_veg,
            recipe=[
                RecipeContextLine(name=ri.ingredient.name, qty=ri.qty, unit=ri.ingredient.unit)
                for ri in item.recipe
            ],
        )
        try:
            response = await ai.estimate_nutrition(request)
        except AIServiceError as exc:
            out.failed.append(NutritionEnrichFailure(item_id=item_id, error=str(exc)))
            continue

        record = await session.get(NutritionEstimateRecord, item_id)
        if record is None:
            record = NutritionEstimateRecord(item_id=item_id)
            session.add(record)
        record.estimate = response.estimate.model_dump()
        record.status = "DRAFT"  # re-enrichment always needs fresh review
        record.model = response.model
        record.prompt_version = response.prompt_version
        record.reviewed_by = None
        out.enriched.append(NutritionOut.model_validate(record))

    if out.enriched:
        audit.record(
            session,
            actor=admin,
            action="nutrition.enrich",
            entity="nutrition",
            detail={"item_ids": [n.item_id for n in out.enriched]},
        )
        await session.commit()
    return out


@router.get("", response_model=list[NutritionOut])
async def list_estimates(
    session: SessionDep,
    admin: User = AdminUser,
    status: str | None = None,
) -> list[NutritionOut]:
    stmt = select(NutritionEstimateRecord).order_by(NutritionEstimateRecord.item_id)
    if status:
        stmt = stmt.where(NutritionEstimateRecord.status == status)
    rows = (await session.scalars(stmt)).all()
    return [NutritionOut.model_validate(r) for r in rows]


@router.post("/{item_id}/status", response_model=NutritionOut)
async def set_status(
    item_id: int,
    body: NutritionStatusIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> NutritionOut:
    """The human gate: only APPROVED estimates reach the public menu."""
    record = await session.get(NutritionEstimateRecord, item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No nutrition estimate for this item")
    if record.status == body.status:
        raise HTTPException(status_code=409, detail=f"Already {body.status}")
    previous = record.status
    record.status = body.status
    record.reviewed_by = admin.id
    audit.record(
        session,
        actor=admin,
        action="nutrition.status",
        entity=f"menu_item:{item_id}",
        detail={"from": previous, "to": body.status},
    )
    await session.commit()
    await session.refresh(record)  # reload server-side updated_at
    await events.publish_menu_event(
        "menu.nutrition", item_id=item_id, detail={"status": body.status}
    )
    return NutritionOut.model_validate(record)
