"""Admin order management (Phase 2): list, modify items, cancel, refund.

/api/v1/admin/orders                 — filterable order list (newest first)
/api/v1/admin/orders/{id}/items      — replace items pre-COOKING (totals recomputed)
/api/v1/admin/orders/{id}/cancel     — cancel with a recorded reason
/api/v1/admin/orders/{id}/refund     — provider refund (Hard Rule 1 interface)
                                       + REFUNDED via the state machine

Kitchen staff keep using POST /api/v1/orders/{id}/status; these routes are
admin/owner-only. All mutations audit + publish order events (Hard Rule 4).
"""

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Order, User
from dosadash_api.db.session import get_session
from dosadash_api.routers.orders import ProviderDep, _load_order, _order_out
from dosadash_api.services import order_service
from dosadash_shared import AdminCancelIn, ModifyItemsIn, OrderOut, OrderState, RefundIn, Role

router = APIRouter(prefix="/api/v1/admin/orders", tags=["admin:orders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


@router.get("", response_model=list[OrderOut])
async def list_orders(
    session: SessionDep,
    admin: User = AdminUser,
    status: OrderState | None = None,
    user_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OrderOut]:
    stmt = select(Order.id).order_by(Order.placed_at.desc(), Order.id.desc())
    if status is not None:
        stmt = stmt.where(Order.status == status)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    order_ids = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return [await _order_out(session, await _load_order(session, oid)) for oid in order_ids]


@router.patch("/{order_id}/items", response_model=OrderOut)
async def modify_items(
    order_id: int,
    body: ModifyItemsIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> OrderOut:
    order = await _load_order(session, order_id)
    try:
        await order_service.modify_items(session, order=order, items_in=body.items, actor=admin)
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except order_service.NotModifiable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, await _load_order(session, order_id))


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: int,
    body: AdminCancelIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> OrderOut:
    order = await _load_order(session, order_id)
    try:
        await order_service.transition(
            session, order=order, target=OrderState.CANCELLED, actor=admin, note=body.reason
        )
    except order_service.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, order)


@router.post("/{order_id}/refund", response_model=OrderOut)
async def refund_order(
    order_id: int,
    body: RefundIn,
    session: SessionDep,
    provider: ProviderDep,
    admin: User = AdminUser,
) -> OrderOut:
    order = await _load_order(session, order_id)
    try:
        await order_service.refund(
            session,
            order=order,
            actor=admin,
            provider=provider,
            amount=body.amount,
            reason=body.reason,
        )
    except order_service.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except order_service.RefundNotPossible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Payment provider refund failed") from exc
    return await _order_out(session, order)
