"""Mock-aggregator ingest (Phase 7, docs/04 O12): signed webhook → the SAME
order pipeline every other channel uses.

Multi-channel routing is the point: an aggregator order becomes a normal
`orders` row (channel = MOCK_AGGREGATOR) driven by the one state machine,
lights up the KDS over the same WebSocket fan-out, and shows up in every
report. Nothing here bypasses order_service — item ids are DB-validated,
totals are computed server-side, kitchen pause/hours are enforced.

Prepaid semantics: the aggregator collected payment, so the Payment row is
created through AggregatorPrepaidProvider (Hard Rule 1 interface) and
immediately marked CAPTURED — settlement is the aggregator's problem.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import AggregatorOrder, MenuItem, Order, Payment, User
from dosadash_api.providers import AggregatorPrepaidProvider
from dosadash_api.services import order_service
from dosadash_shared import (
    AggregatorWebhookIn,
    ChannelType,
    OrderItemIn,
    PaymentStatus,
    Role,
)


class UnknownItems(Exception):
    """Aggregator listing drifted from the kitchen menu — refuse loudly."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"unknown menu items: {', '.join(names)}")


async def _upsert_customer(session: AsyncSession, payload: AggregatorWebhookIn) -> User:
    user = await session.scalar(select(User).where(User.phone == payload.customer.phone))
    if user is None:
        user = User(
            phone=payload.customer.phone,
            name=f"{payload.customer.name} (via {payload.aggregator})",
            role=Role.CUSTOMER,
        )
        session.add(user)
        await session.flush()
    return user


async def _resolve_items(session: AsyncSession, payload: AggregatorWebhookIn) -> list[OrderItemIn]:
    """Aggregators sync menus by name — case-insensitive exact match only."""
    names = {item.name.lower(): item for item in payload.items}
    rows = (
        await session.scalars(select(MenuItem).where(func.lower(MenuItem.name).in_(names.keys())))
    ).all()
    by_name = {row.name.lower(): row for row in rows}
    unknown = sorted(n for n in names if n not in by_name)
    if unknown:
        raise UnknownItems(unknown)
    return [
        OrderItemIn(item_id=by_name[lowered].id, qty=item.qty) for lowered, item in names.items()
    ]


async def ingest(session: AsyncSession, payload: AggregatorWebhookIn) -> tuple[Order, bool]:
    """One webhook delivery → (order, was_duplicate). Idempotent: retries of
    the same (aggregator, external_order_id) return the original order."""
    existing = await session.scalar(
        select(AggregatorOrder).where(
            AggregatorOrder.aggregator == payload.aggregator,
            AggregatorOrder.external_order_id == payload.external_order_id,
        )
    )
    if existing is not None:
        return await session.get(Order, existing.order_id), True

    user = await _upsert_customer(session, payload)
    items_in = await _resolve_items(session, payload)
    order = await order_service.create_order(
        session,
        user=user,
        items_in=items_in,
        provider=AggregatorPrepaidProvider(),
        channel=ChannelType.MOCK_AGGREGATOR,
    )
    # prepaid: settle immediately (the aggregator already charged the customer)
    payment = await session.scalar(select(Payment).where(Payment.order_id == order.id))
    payment.status = PaymentStatus.CAPTURED
    payment.provider_payment_id = f"agg_{payload.aggregator}_{payload.external_order_id}"
    session.add(
        AggregatorOrder(
            aggregator=payload.aggregator,
            external_order_id=payload.external_order_id,
            order_id=order.id,
        )
    )
    await session.commit()
    return order, False
