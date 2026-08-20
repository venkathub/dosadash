"""Order endpoints.

POST /api/v1/orders               — checkout (auth) → PLACED + provider order
POST /api/v1/orders/{id}/pay      — verify payment signature → CAPTURED
POST /api/v1/orders/{id}/cancel   — customer cancel (PLACED only)
POST /api/v1/orders/{id}/status   — staff transition (RBAC + audit)
GET  /api/v1/orders               — my order history (newest first)
GET  /api/v1/orders/{id}          — detail (owner or staff)
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.config import get_settings
from dosadash_api.db.models import Coupon, Order, OrderItem, Payment
from dosadash_api.db.session import get_session
from dosadash_api.providers import (
    MockPaymentProvider,
    PaymentProvider,
    select_payment_provider,
)
from dosadash_api.services import coupon_service, order_service
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_api.services.order_service import STAFF_ROLES
from dosadash_shared import (
    DishQCIn,
    DishQCResult,
    OrderCreateIn,
    OrderItemIn,
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
    """Selected via settings (Hard Rule 1): Razorpay TEST when keys exist."""
    s = get_settings()
    return select_payment_provider(
        provider=s.payment_provider,
        razorpay_key_id=s.razorpay_key_id,
        razorpay_key_secret=s.razorpay_key_secret,
    )


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
    if order.coupon_id is not None:
        out.coupon_code = await session.scalar(
            select(Coupon.code).where(Coupon.id == order.coupon_id)
        )
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
            coupon_code=body.coupon_code,
        )
    except coupon_service.CouponError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except order_service.KitchenPaused as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except order_service.OutsideBusinessHours as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except order_service.NotPermitted as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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


class QCPhotoIn(BaseModel):
    """Same bounds as the ai-side DishQCIn so junk fails at the api edge."""

    image_base64: str = Field(min_length=8, max_length=10_000_000)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]


@router.post("/{order_id}/qc-photo", response_model=DishQCResult)
async def qc_photo(
    order_id: int,
    body: QCPhotoIn,
    user: CurrentUser,
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
) -> DishQCResult:
    """Dish-photo QC (Phase 7): staff photographs the plated/packed order
    before dispatch; the ai service returns a deterministic verdict computed
    from VLM observations. Stateless — the KDS shows the verdict inline."""
    if user.role not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Staff only")
    order = await _load_order(session, order_id)
    expected = [line.item.name for line in order.items]
    if not expected:
        raise HTTPException(status_code=409, detail="Order has no items to check")
    try:
        return await ai.qc_dish(
            DishQCIn(
                image_base64=body.image_base64,
                mime_type=body.mime_type,  # DishQCIn re-validates the Literal
                expected_dishes=expected,
                session_id=f"kds:{user.id}",
            )
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="QC service unavailable") from exc


@router.post("/{order_id}/pay/demo", response_model=OrderOut)
async def pay_demo(
    order_id: int, user: CurrentUser, session: SessionDep, provider: ProviderDep
) -> OrderOut:
    """Demo-mode capture for the mock provider ONLY: the server self-signs.

    Exists so the web demo can complete checkout without a real gateway.
    Returns 400 for any non-mock provider (Razorpay flow replaces this).
    """
    order = await _load_order(session, order_id)
    if order.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your order")
    payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is None or payment.provider != "mock" or payment.provider_order_id is None:
        raise HTTPException(status_code=400, detail="Demo capture only works with mock payments")
    if not isinstance(provider, MockPaymentProvider):
        raise HTTPException(status_code=400, detail="Demo capture unavailable")
    signature = provider.sign(order_id=payment.provider_order_id, payment_id=f"pay_demo_{order.id}")
    await order_service.verify_payment(
        session,
        order=order,
        payment_id=f"pay_demo_{order.id}",
        signature=signature,
        provider=provider,
    )
    return await _order_out(session, order)


@router.post("/{order_id}/reorder", response_model=OrderOut, status_code=201)
async def reorder(
    order_id: int, user: CurrentUser, session: SessionDep, provider: ProviderDep
) -> OrderOut:
    """1-tap reorder: new order from a past order's items (re-validated
    against the current menu — sold-out items → 409)."""
    old = await _load_order(session, order_id)
    if old.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your order")
    items_in = [
        OrderItemIn(item_id=oi.item_id, qty=oi.qty, customizations=oi.customizations)
        for oi in old.items
    ]
    try:
        new_order = await order_service.create_order(
            session,
            user=user,
            items_in=items_in,
            provider=provider,
            address_id=old.address_id,
        )
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _order_out(session, await _load_order(session, new_order.id))


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
