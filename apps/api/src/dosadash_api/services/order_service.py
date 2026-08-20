"""Order domain service — the ONLY place order state transitions happen
(CLAUDE.md convention), plus DB-validated checkout (precursor of Hard Rule 2).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.db.models import (
    Address,
    Brand,
    MenuItem,
    Order,
    OrderItem,
    Payment,
    Settings,
    StaffAction,
    User,
)
from dosadash_api.providers import PaymentProvider
from dosadash_api.services import coupon_service, memory_service
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_ml.eta.features import heuristic_eta_minutes
from dosadash_shared import (
    ChannelType,
    EtaRequest,
    OrderItemIn,
    OrderState,
    PaymentStatus,
    Role,
    availability,
)

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


class KitchenPaused(OrderError):
    def __init__(self) -> None:
        super().__init__("kitchen is paused — not accepting orders right now")


class OutsideBusinessHours(OrderError):
    def __init__(self) -> None:
        super().__init__("kitchen is closed — outside business hours")


class NotModifiable(OrderError):
    def __init__(self, status: OrderState) -> None:
        super().__init__(f"order in {status} can no longer be modified (pre-COOKING only)")


class RefundNotPossible(OrderError):
    pass


class InvalidTransition(OrderError):
    def __init__(self, current: OrderState, target: OrderState) -> None:
        super().__init__(f"cannot transition {current} -> {target}")


class NotPermitted(OrderError):
    pass


def can_transition(current: OrderState, target: OrderState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


MODIFIABLE_STATES = frozenset({OrderState.PLACED, OrderState.CONFIRMED})
ADMIN_ROLES = {Role.ADMIN, Role.OWNER}


async def _validate_items(
    session: AsyncSession, items_in: list[OrderItemIn]
) -> tuple[dict[int, int], dict[int, dict[str, Any] | None], dict[int, MenuItem]]:
    """DB-validate every item_id (Hard Rule 2 precursor) and return
    (qty by item, customizations by item, MenuItem by id)."""
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
    return wanted, customizations, found


def _totals(wanted: dict[int, int], found: dict[int, MenuItem]) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = Decimal("0")
    gst = Decimal("0")
    for item_id, qty in wanted.items():
        line_total = found[item_id].price * qty
        subtotal += line_total
        gst += line_total * found[item_id].gst_rate / 100
    gst = gst.quantize(Decimal("0.01"))
    return subtotal, gst, subtotal + gst


async def _predict_eta_minutes(
    ai: AIClient | None, *, max_prep: int, total_qty: int, n_lines: int
) -> int:
    """Champion ETA via the ai service; heuristic fallback keeps checkout
    working (and fast) when the model/service is unavailable."""
    if ai is None:
        ai = get_ai_client()
    try:
        response = await ai.predict_eta(
            EtaRequest(max_prep_minutes=max_prep, total_qty=total_qty, n_lines=n_lines)
        )
        return response.eta_minutes
    except (AIServiceError, ValueError):
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
        return heuristic_eta_minutes(max_prep=max_prep, total_qty=total_qty, when=now_ist)


async def create_order(
    session: AsyncSession,
    *,
    user: User,
    items_in: list[OrderItemIn],
    provider: PaymentProvider,
    channel: ChannelType = ChannelType.WEB,
    address_id: int | None = None,
    coupon_code: str | None = None,
    ai: AIClient | None = None,
) -> Order:
    """Checkout: every item_id validated against the DB, totals computed
    server-side, provider order created. Order starts in PLACED."""
    settings_row = await session.get(Settings, 1)
    if settings_row is not None and settings_row.kitchen_paused:
        raise KitchenPaused
    if settings_row is not None and not availability.is_open(settings_row.business_hours):
        raise OutsideBusinessHours

    wanted, customizations, found = await _validate_items(session, items_in)
    off_schedule = [m for m in found.values() if not availability.item_on_schedule(m.schedule)]
    if off_schedule:
        # Tell the customer WHEN the dish is served, not just that it isn't.
        labelled = sorted(
            f"{m.name} (served {availability.serving_windows_text(m.schedule)})"
            for m in off_schedule
        )
        raise ItemsUnavailable(labelled)

    if address_id is not None:
        address = await session.get(Address, address_id)
        if address is None or address.user_id != user.id:
            raise NotPermitted("address does not belong to this user")

    subtotal, gst, total = _totals(wanted, found)
    coupon, discount = None, Decimal("0")
    if coupon_code:
        # coupon_service.CouponError propagates — the router maps it to 400.
        coupon, discount = await coupon_service.resolve(
            session, code=coupon_code, user_id=user.id, subtotal=subtotal
        )
        gst = coupon_service.discounted_gst(gst, subtotal, discount)
        total = subtotal - discount + gst
    eta_minutes = await _predict_eta_minutes(
        ai,
        max_prep=max(m.prep_minutes for m in found.values()),
        total_qty=sum(wanted.values()),
        n_lines=len(wanted),
    )

    brand_id = found[next(iter(wanted))].brand_id or (
        await session.scalar(select(Brand.id).limit(1))
    )
    order = Order(
        user_id=user.id,
        brand_id=brand_id,
        channel=channel,
        status=OrderState.PLACED,
        subtotal=subtotal,
        discount=discount,
        gst=gst,
        total=total,
        coupon_id=coupon.id if coupon else None,
        address_id=address_id,
        eta_predicted=datetime.now(UTC) + timedelta(minutes=eta_minutes),
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
    if coupon is not None:
        coupon_service.redeem(session, coupon=coupon, user_id=user.id, order_id=order.id)

    provider_order = await provider.create_order(amount=total)
    session.add(
        Payment(
            order_id=order.id,
            provider=provider_order.provider,
            provider_order_id=provider_order.provider_order_id,
            status=PaymentStatus.CREATED,
        )
    )
    memory_service.record_order_episode(session, order=order, found=found)  # Phase 6
    await session.commit()
    await session.refresh(order, ["placed_at"])
    await events.publish_order_event("order.created", order)
    return order


async def transition(
    session: AsyncSession,
    *,
    order: Order,
    target: OrderState,
    actor: User,
    note: str | None = None,
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
    if target == OrderState.DELIVERED and order.delivered_at is None:
        order.delivered_at = datetime.now(UTC)  # ETA-model label (Phase 5)
    if actor.role in STAFF_ROLES:
        detail: dict[str, Any] = {"from": previous.value, "to": target.value}
        if note:
            detail["note"] = note
        session.add(
            StaffAction(
                user_id=actor.id,
                action="order.status",
                entity=f"order:{order.id}",
                detail=detail,
            )
        )
    await session.commit()
    await events.publish_order_event("order.status", order)
    return order


async def modify_items(
    session: AsyncSession, *, order: Order, items_in: list[OrderItemIn], actor: User
) -> Order:
    """Admin replaces the item list of a pre-COOKING order; totals recomputed
    server-side, every item_id DB-validated. Any captured-payment difference
    is recorded in the audit detail for the owner to settle via refund."""
    if actor.role not in ADMIN_ROLES:
        raise NotPermitted("admin/owner only")
    if order.status not in MODIFIABLE_STATES:
        raise NotModifiable(order.status)

    wanted, customizations, found = await _validate_items(session, items_in)
    old_total = order.total
    subtotal, gst, total = _totals(wanted, found)

    for old_item in list(order.items):
        await session.delete(old_item)
    await session.flush()  # DELETEs first, so the FK is never blanked out
    order.items = [
        OrderItem(
            item=found[item_id],
            qty=qty,
            unit_price=found[item_id].price,
            customizations=customizations[item_id],
        )
        for item_id, qty in wanted.items()
    ]
    order.subtotal, order.gst, order.total = subtotal, gst, total
    session.add(
        StaffAction(
            user_id=actor.id,
            action="order.modify",
            entity=f"order:{order.id}",
            detail={
                "old_total": str(old_total),
                "new_total": str(total),
                "items": [{"item_id": i, "qty": q} for i, q in wanted.items()],
            },
        )
    )
    await session.commit()
    await events.publish_order_event("order.updated", order)
    return order


async def refund(
    session: AsyncSession,
    *,
    order: Order,
    actor: User,
    provider: PaymentProvider,
    amount: Decimal | None = None,
    reason: str,
) -> Order:
    """Refund the captured payment via the provider, then move the order to
    REFUNDED through the state machine (only DELIVERED/CANCELLED qualify)."""
    if actor.role not in ADMIN_ROLES:
        raise NotPermitted("admin/owner only")
    if not can_transition(order.status, OrderState.REFUNDED):
        raise InvalidTransition(order.status, OrderState.REFUNDED)

    payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    if payment is None or payment.status != PaymentStatus.CAPTURED:
        raise RefundNotPossible("no captured payment to refund")
    if not payment.provider_payment_id:
        raise RefundNotPossible("captured payment id unknown — cannot call provider refund")

    amt = amount if amount is not None else order.total
    if amt <= 0 or amt > order.total:
        raise RefundNotPossible(f"refund amount must be within (0, {order.total}]")

    result = await provider.refund(payment_id=payment.provider_payment_id, amount=amt)
    payment.status = PaymentStatus.REFUNDED
    payment.refund_id = result.refund_id
    session.add(
        StaffAction(
            user_id=actor.id,
            action="order.refund",
            entity=f"order:{order.id}",
            detail={"amount": str(amt), "reason": reason, "refund_id": result.refund_id},
        )
    )
    return await transition(
        session, order=order, target=OrderState.REFUNDED, actor=actor, note=reason
    )


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
    payment.provider_payment_id = payment_id  # needed for the admin refund flow
    payment.signature_verified = True
    await session.commit()
    return payment
