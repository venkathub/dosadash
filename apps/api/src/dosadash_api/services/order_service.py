"""Order domain service — the ONLY place order state transitions happen
(CLAUDE.md convention), plus DB-validated checkout (precursor of Hard Rule 2).
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.db.models import (
    Brand,
    MenuItem,
    Order,
    OrderItem,
    Payment,
    StaffAction,
    User,
)
from dosadash_api.providers import PaymentProvider
from dosadash_shared import ChannelType, OrderItemIn, OrderState, PaymentStatus, Role

STAFF_ROLES = {Role.KITCHEN_STAFF, Role.ADMIN, Role.OWNER}

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PLACED: frozenset({OrderState.CONFIRMED, OrderState.CANCELLED}),
    OrderState.CONFIRMED: frozenset({OrderState.COOKING, OrderState.CANCELLED}),
    OrderState.COOKING: frozenset({OrderState.READY, OrderState.CANCELLED}),
    OrderState.READY: frozenset({OrderState.OUT_FOR_DELIVERY}),
    OrderState.OUT_FOR_DELIVERY: frozenset({OrderState.DELIVERED}),
    OrderState.DELIVERED: frozenset({OrderState.REFUNDED}),
    OrderState.CANCELLED: frozenset({OrderState.REFUNDED}),
    OrderState.REFUNDED: frozenset(),
}


class OrderError(Exception):
    """Base for order domain errors."""


class ItemsNotFound(OrderError):
    def __init__(self, item_ids: set[int]) -> None:
        self.item_ids = item_ids
        super().__init__(f"unknown item ids: {sorted(item_ids)}")


class ItemsUnavailable(OrderError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"items unavailable: {names}")


class InvalidTransition(OrderError):
    def __init__(self, current: OrderState, target: OrderState) -> None:
        super().__init__(f"cannot transition {current} -> {target}")


class NotPermitted(OrderError):
    pass


def can_transition(current: OrderState, target: OrderState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


async def create_order(
    session: AsyncSession,
    *,
    user: User,
    items_in: list[OrderItemIn],
    provider: PaymentProvider,
    channel: ChannelType = ChannelType.WEB,
    address_id: int | None = None,
) -> Order:
    """Checkout: every item_id validated against the DB, totals computed
    server-side, provider order created. Order starts in PLACED."""
    wanted: dict[int, int] = {}
    customizations: dict[int, dict[str, Any] | None] = {}
    for line in items_in:
        wanted[line.item_id] = wanted.get(line.item_id, 0) + line.qty
        customizations[line.item_id] = line.customizations

    rows = (await session.scalars(select(MenuItem).where(MenuItem.id.in_(wanted)))).all()
    found = {m.id: m for m in rows}
    missing = set(wanted) - set(found)
    if missing:
        raise ItemsNotFound(missing)
    sold_out = [m.name for m in found.values() if not m.is_available]
    if sold_out:
        raise ItemsUnavailable(sorted(sold_out))

    subtotal = Decimal("0")
    gst = Decimal("0")
    for item_id, qty in wanted.items():
        m = found[item_id]
        line_total = m.price * qty
        subtotal += line_total
        gst += line_total * m.gst_rate / 100
    gst = gst.quantize(Decimal("0.01"))
    total = subtotal + gst

    brand_id = found[next(iter(wanted))].brand_id or (
        await session.scalar(select(Brand.id).limit(1))
    )
    order = Order(
        user_id=user.id,
        brand_id=brand_id,
        channel=channel,
        status=OrderState.PLACED,
        subtotal=subtotal,
        gst=gst,
        total=total,
        address_id=address_id,
    )
    order.items = [
        OrderItem(
            item=found[item_id],
            qty=qty,
            unit_price=found[item_id].price,
            customizations=customizations[item_id],
        )
        for item_id, qty in wanted.items()
    ]
    session.add(order)
    await session.flush()

    provider_order = await provider.create_order(amount=total)
    session.add(
        Payment(
            order_id=order.id,
            provider=provider_order.provider,
            provider_order_id=provider_order.provider_order_id,
            status=PaymentStatus.CREATED,
        )
    )
    await session.commit()
    await session.refresh(order, ["placed_at"])
    await events.publish_order_event("order.created", order)
    return order


async def transition(
    session: AsyncSession, *, order: Order, target: OrderState, actor: User
) -> Order:
    """Enforced transition with per-role rules + audit trail for staff."""
    if actor.role == Role.CUSTOMER:
        if order.user_id != actor.id:
            raise NotPermitted("not your order")
        if target != OrderState.CANCELLED or order.status != OrderState.PLACED:
            raise NotPermitted("customers may only cancel orders that are still PLACED")
    elif actor.role not in STAFF_ROLES:
        raise NotPermitted("role cannot manage orders")

    if not can_transition(order.status, target):
        raise InvalidTransition(order.status, target)

    previous = order.status
    order.status = target
    if actor.role in STAFF_ROLES:
        session.add(
            StaffAction(
                user_id=actor.id,
                action="order.status",
                entity=f"order:{order.id}",
                detail={"from": previous.value, "to": target.value},
            )
        )
    await session.commit()
    await events.publish_order_event("order.status", order)
    return order


async def verify_payment(
    session: AsyncSession,
    *,
    order: Order,
    payment_id: str,
    signature: str,
    provider: PaymentProvider,
) -> Payment:
    payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is None or payment.provider_order_id is None:
        raise OrderError("no payment pending for this order")
    if not provider.verify_signature(
        order_id=payment.provider_order_id, payment_id=payment_id, signature=signature
    ):
        raise NotPermitted("payment signature verification failed")
    payment.status = PaymentStatus.CAPTURED
    payment.signature_verified = True
    await session.commit()
    return payment
