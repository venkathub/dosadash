"""Admin purchase orders (Phase 6): review + approve the inventory agent.

/api/v1/admin/purchase-orders            — filterable list (newest first)
/api/v1/admin/purchase-orders/draft-now  — run the agent on demand
/api/v1/admin/purchase-orders/{id}       — detail with lines
/{id}/items/{ingredient_id}              — owner qty edit pre-approval
/{id}/approve|reject|receive|cancel      — state machine transitions

Plus POST /api/v1/internal/po/decision — the Telegram owner-approval path
(bot forwards button taps with tg_user_id; RBAC re-checked here, never in
the bot — Hard Rule 10).

All mutations audit + publish inventory.* events (Hard Rule 4).
"""

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import PurchaseOrder, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit, po_service
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_api.services.po_notify import notify_owners_po_drafted
from dosadash_shared import (
    InventoryDraftRequest,
    POItemPatchIn,
    POState,
    PurchaseOrderDetailOut,
    PurchaseOrderOut,
    Role,
)

router = APIRouter(tags=["admin:purchase-orders"])

PREFIX = "/api/v1/admin/purchase-orders"

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

_EDITABLE = {POState.DRAFT, POState.PENDING_APPROVAL}


def _po_out(po: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        supplier_name=po.supplier.name if po.supplier else None,
        status=po.status,
        source=po.source,
        rationale=po.rationale,
        coverage_days=po.coverage_days,
        expected_cost=po.expected_cost,
        model=po.model,
        prompt_version=po.prompt_version,
        approved_by=po.approved_by,
        approved_at=po.approved_at,
        received_at=po.received_at,
        created_at=po.created_at,
    )


def _po_detail(po: PurchaseOrder) -> PurchaseOrderDetailOut:
    return PurchaseOrderDetailOut(
        **_po_out(po).model_dump(),
        items=[
            {
                "ingredient_id": item.ingredient_id,
                "ingredient_name": item.ingredient.name,
                "unit": item.unit,
                "qty": item.qty,
                "unit_cost": item.unit_cost,
                "reason": item.reason,
            }
            for item in po.items
        ],
    )


async def _get_po(session: AsyncSession, po_id: int) -> PurchaseOrder:
    po = await po_service.get_po(session, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po


def _recompute_expected_cost(po: PurchaseOrder) -> None:
    total = sum((i.qty * i.unit_cost for i in po.items if i.unit_cost is not None), start=0)
    po.expected_cost = total or None


@router.get(PREFIX, response_model=list[PurchaseOrderOut])
async def list_pos(
    session: SessionDep,
    admin: User = AdminUser,
    status: POState | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[PurchaseOrderOut]:
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.supplier))
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(PurchaseOrder.status == status)
    return [_po_out(po) for po in (await session.scalars(stmt)).all()]


@router.post(f"{PREFIX}/draft-now", response_model=list[PurchaseOrderDetailOut])
async def draft_now(
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
    admin: User = AdminUser,
    coverage_days: Annotated[int, Query(ge=1, le=14)] = 7,
) -> list[PurchaseOrderDetailOut]:
    """Run the inventory agent on demand (same path as the nightly task)."""
    try:
        result = await ai.draft_inventory_pos(
            InventoryDraftRequest(coverage_days=coverage_days, session_id=f"admin:{admin.id}")
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Inventory agent unavailable") from exc

    created, _skipped = await po_service.persist_agent_drafts(session, result)
    audit.record(
        session,
        actor=admin,
        action="po.draft_now",
        entity="purchase_order",
        detail={"created": [po.id for po in created], "fallback": result.fallback},
    )
    await session.commit()
    for po in created:
        await events.publish_inventory_event("inventory.po_drafted", detail={"po_id": po.id})
    await notify_owners_po_drafted(session, [po.id for po in created])
    return [_po_detail(await _get_po(session, po.id)) for po in created]


@router.get(PREFIX + "/{po_id}", response_model=PurchaseOrderDetailOut)
async def get_po(
    po_id: int, session: SessionDep, admin: User = AdminUser
) -> PurchaseOrderDetailOut:
    return _po_detail(await _get_po(session, po_id))


@router.patch(PREFIX + "/{po_id}/items/{ingredient_id}", response_model=PurchaseOrderDetailOut)
async def edit_line(
    po_id: int,
    ingredient_id: int,
    body: POItemPatchIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> PurchaseOrderDetailOut:
    po = await _get_po(session, po_id)
    if po.status not in _EDITABLE:
        raise HTTPException(status_code=409, detail=f"Cannot edit a {po.status.value} order")
    item = next((i for i in po.items if i.ingredient_id == ingredient_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Line not found")
    old_qty = item.qty
    item.qty = body.qty
    _recompute_expected_cost(po)
    audit.record(
        session,
        actor=admin,
        action="po.edit_line",
        entity=f"purchase_order:{po.id}",
        detail={"ingredient_id": ingredient_id, "from": str(old_qty), "to": str(body.qty)},
    )
    await session.commit()
    return _po_detail(po)


async def _transition(
    session: AsyncSession,
    po: PurchaseOrder,
    action: Literal["approve", "reject", "receive", "cancel"],
    actor: User,
) -> PurchaseOrderDetailOut:
    try:
        if action == "approve":
            po_service.approve(po, actor_id=actor.id)
        elif action == "reject":
            po_service.reject(po, actor_id=actor.id)
        elif action == "receive":
            await po_service.receive(session, po)
        else:
            po_service.cancel(po)
    except po_service.InvalidPOTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        session,
        actor=actor,
        action=f"po.{action}",
        entity=f"purchase_order:{po.id}",
        detail={"status": po.status.value},
    )
    await session.commit()
    await events.publish_inventory_event(
        f"inventory.po_{po.status.value.lower()}", detail={"po_id": po.id}
    )
    return _po_detail(po)


@router.post(PREFIX + "/{po_id}/approve", response_model=PurchaseOrderDetailOut)
async def approve_po(
    po_id: int, session: SessionDep, admin: User = AdminUser
) -> PurchaseOrderDetailOut:
    return await _transition(session, await _get_po(session, po_id), "approve", admin)


@router.post(PREFIX + "/{po_id}/reject", response_model=PurchaseOrderDetailOut)
async def reject_po(
    po_id: int, session: SessionDep, admin: User = AdminUser
) -> PurchaseOrderDetailOut:
    return await _transition(session, await _get_po(session, po_id), "reject", admin)


@router.post(PREFIX + "/{po_id}/receive", response_model=PurchaseOrderDetailOut)
async def receive_po(
    po_id: int, session: SessionDep, admin: User = AdminUser
) -> PurchaseOrderDetailOut:
    """Goods in: stock incremented line by line (closes the inventory loop)."""
    return await _transition(session, await _get_po(session, po_id), "receive", admin)


@router.post(PREFIX + "/{po_id}/cancel", response_model=PurchaseOrderDetailOut)
async def cancel_po(
    po_id: int, session: SessionDep, admin: User = AdminUser
) -> PurchaseOrderDetailOut:
    return await _transition(session, await _get_po(session, po_id), "cancel", admin)


# ------------------------------------------------- Telegram approval (internal)


class PODecisionIn(BaseModel):
    tg_user_id: int
    po_id: int
    action: Literal["approve", "reject"]


class PODecisionOut(BaseModel):
    ok: bool
    status: str | None = None
    detail: str | None = None


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/api/v1/internal/po/decision", response_model=PODecisionOut)
async def po_decision(
    body: PODecisionIn,
    session: SessionDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> PODecisionOut:
    """Owner tapped Approve/Reject in Telegram. The bot is a dumb pipe: role
    and transition legality are enforced HERE."""
    _check_internal_token(x_internal_token)
    user = await session.scalar(select(User).where(User.tg_user_id == body.tg_user_id))
    if user is None or user.role not in (Role.ADMIN, Role.OWNER):
        return PODecisionOut(ok=False, detail="This Telegram account cannot approve orders.")
    po = await po_service.get_po(session, body.po_id)
    if po is None:
        return PODecisionOut(ok=False, detail="Purchase order not found.")
    try:
        result = await _transition(session, po, body.action, user)
    except HTTPException as exc:
        return PODecisionOut(ok=False, detail=str(exc.detail))
    return PODecisionOut(ok=True, status=result.status.value)
