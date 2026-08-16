"""Order endpoints.

POST /api/v1/orders               — checkout (auth) → PLACED + provider order
POST /api/v1/orders/{id}/pay      — verify payment signature → CAPTURED
POST /api/v1/orders/{id}/cancel   — customer cancel (PLACED only)
POST /api/v1/orders/{id}/status   — staff transition (RBAC + audit)
GET  /api/v1/orders               — my order history (newest first)
GET  /api/v1/orders/{id}          — detail (owner or staff)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.db.models import Order, OrderItem, Payment
from dosadash_api.db.session import get_session
from dosadash_api.providers import MockPaymentProvider, PaymentProvider
from dosadash_api.services import order_service
from dosadash_api.services.order_service import STAFF_ROLES
from dosadash_shared import (
    OrderCreateIn,
    OrderItemOut,
    OrderOut,
    OrderState,
    PayIn,
    PaymentOut,
    StatusUpdateIn,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_payment_provider() -> PaymentProvider:
    """Phase 1: mock provider; Razorpay TEST joins via the same interface."""
    return MockPaymentProvider()


ProviderDep = Annotated[PaymentProvider, Depends(get_payment_provider)]

_LOAD = (selectinload(Order.items).selectinload(OrderItem.item),)


async def _order_out(session: AsyncSession, order: Order) -> OrderOut:
    out = OrderOut.model_validate(order)
    out.items = [
        OrderItemOut(
            item_id=oi.item_id,
            name=oi.item.name if oi.item else "",
            qty=oi.qty,
            unit_price=oi.unit_price,
            customizations=oi.customizations,
        )
        for oi in order.items
    ]
    payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    out.payment = PaymentOut.model_validate(payment) if payment else None
    return out


async def _load_order(session: AsyncSession, order_id: int) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).options(*_LOAD))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("", response_model=OrderOut, status_code=201)
async def checkout(
    body: OrderCreateIn, user: CurrentUser, session: SessionDep, provider: ProviderDep
) -> OrderOut:
    try:
        order = await order_service.create_order(
            session,
            user=user,
            items_in=body.items,
            provider=provider,
            address_id=body.address_id,
        )
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, await _load_order(session, order.id))


@router.post("/{order_id}/pay", response_model=OrderOut)
async def pay(
    order_id: int, body: PayIn, user: CurrentUser, session: SessionDep, provider: ProviderDep
) -> OrderOut:
    order = await _load_order(session, order_id)
    if order.user_id != user.id and user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Not your order")
    try:
        await order_service.verify_payment(
            session,
            order=order,
            payment_id=body.payment_id,
            signature=body.signature,
            provider=provider,
        )
    except order_service.NotPermitted as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except order_service.OrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel(order_id: int, user: CurrentUser, session: SessionDep) -> OrderOut:
    order = await _load_order(session, order_id)
    try:
        await order_service.transition(
            session, order=order, target=OrderState.CANCELLED, actor=user
        )
    except order_service.NotPermitted as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except order_service.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, order)


@router.post("/{order_id}/status", response_model=OrderOut)
async def set_status(
    order_id: int, body: StatusUpdateIn, user: CurrentUser, session: SessionDep
) -> OrderOut:
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Staff only")
    order = await _load_order(session, order_id)
    try:
        await order_service.transition(session, order=order, target=body.status, actor=user)
    except order_service.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except order_service.NotPermitted as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return await _order_out(session, order)


@router.get("", response_model=list[OrderOut])
async def my_orders(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OrderOut]:
    orders = (
        await session.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .options(*_LOAD)
            .order_by(Order.placed_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [await _order_out(session, o) for o in orders]


@router.get("/{order_id}", response_model=OrderOut)
async def order_detail(order_id: int, user: CurrentUser, session: SessionDep) -> OrderOut:
    order = await _load_order(session, order_id)
    if order.user_id != user.id and user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Not your order")
    return await _order_out(session, order)
