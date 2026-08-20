"""Mock-aggregator channel endpoints (Phase 7, docs/04 O12).

POST /api/v1/aggregator/webhook                       — signed order injection
GET  /api/v1/aggregator/orders/{agg}/{external_id}    — signed status poll
POST /api/v1/admin/aggregator/simulate                — demo: fire mock orders

Webhook auth mirrors the Razorpay pattern: HMAC-SHA256 of the raw body with
a shared secret in X-Aggregator-Signature (503 when unconfigured, 403 on a
bad signature). The status poll signs the path identity instead of a body.
Swapping in a real Zomato/Swiggy integration is a payload-mapping exercise,
not an architecture change — everything lands in the one order pipeline.
"""

import hashlib
import hmac
import random
import secrets as sec
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import AggregatorOrder, MenuItem, Order, User
from dosadash_api.db.session import get_session
from dosadash_api.services import aggregator_service, audit, order_service
from dosadash_shared import (
    MOCK_AGGREGATORS,
    AggregatorCustomerIn,
    AggregatorItemIn,
    AggregatorOrderOut,
    AggregatorSimulateIn,
    AggregatorStatusOut,
    AggregatorWebhookIn,
    Role,
)

router = APIRouter(tags=["aggregator"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


def _secret() -> bytes:
    secret = get_settings().aggregator_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Aggregator channel not configured")
    return secret.encode()


def _check_signature(payload: bytes, provided: str) -> None:
    expected = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Bad aggregator signature")


def _out(order: Order, external_order_id: str, duplicate: bool) -> AggregatorOrderOut:
    return AggregatorOrderOut(
        order_id=order.id,
        external_order_id=external_order_id,
        status=order.status.value,
        total=order.total,
        duplicate=duplicate,
    )


async def _ingest_or_http_error(
    session: AsyncSession, payload: AggregatorWebhookIn
) -> tuple[Order, bool]:
    try:
        return await aggregator_service.ingest(session, payload)
    except aggregator_service.UnknownItems as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except order_service.KitchenPaused as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except order_service.OutsideBusinessHours as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/v1/aggregator/webhook", response_model=AggregatorOrderOut, status_code=201)
async def webhook(
    request: Request,
    session: SessionDep,
    x_aggregator_signature: Annotated[str, Header()] = "",
) -> AggregatorOrderOut:
    body = await request.body()
    _check_signature(body, x_aggregator_signature)
    try:
        payload = AggregatorWebhookIn.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    order, duplicate = await _ingest_or_http_error(session, payload)
    return _out(order, payload.external_order_id, duplicate)


@router.get(
    "/api/v1/aggregator/orders/{aggregator}/{external_order_id}",
    response_model=AggregatorStatusOut,
)
async def status_poll(
    aggregator: str,
    external_order_id: str,
    session: SessionDep,
    x_aggregator_signature: Annotated[str, Header()] = "",
) -> AggregatorStatusOut:
    """Aggregator-side status sync (poll model — no body, so the signature
    covers the path identity)."""
    _check_signature(f"{aggregator}|{external_order_id}".encode(), x_aggregator_signature)
    mapping = await session.scalar(
        select(AggregatorOrder).where(
            AggregatorOrder.aggregator == aggregator,
            AggregatorOrder.external_order_id == external_order_id,
        )
    )
    if mapping is None:
        raise HTTPException(status_code=404, detail="Unknown aggregator order")
    order = await session.get(Order, mapping.order_id)
    return AggregatorStatusOut(
        aggregator=aggregator,
        external_order_id=external_order_id,
        order_id=order.id,
        status=order.status.value,
        eta_predicted=order.eta_predicted,
    )


@router.post("/api/v1/admin/aggregator/simulate", response_model=list[AggregatorOrderOut])
async def simulate(
    body: AggregatorSimulateIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> list[AggregatorOrderOut]:
    """Demo lever: inject N random mock-aggregator orders through the SAME
    ingest path the webhook uses — watch the KDS light up."""
    items = (await session.scalars(select(MenuItem).where(MenuItem.is_available))).all()
    orderable = [m for m in items if not m.schedule]  # time-independent demo
    if not orderable:
        raise HTTPException(status_code=409, detail="No orderable menu items")
    results: list[AggregatorOrderOut] = []
    for _ in range(body.count):
        aggregator = random.choice(MOCK_AGGREGATORS)
        suffix = sec.token_hex(4)
        payload = AggregatorWebhookIn(
            aggregator=aggregator,
            external_order_id=f"SIM-{suffix.upper()}",
            customer=AggregatorCustomerIn(
                name=f"Sim Customer {suffix[:4]}",
                phone=f"+9198{random.randint(10_000_000, 99_999_999)}",
            ),
            items=[
                AggregatorItemIn(name=m.name, qty=random.randint(1, 2))
                for m in random.sample(orderable, k=min(len(orderable), random.randint(1, 3)))
            ],
        )
        order, duplicate = await _ingest_or_http_error(session, payload)
        results.append(_out(order, payload.external_order_id, duplicate))
    audit.record(
        session,
        actor=admin,
        action="aggregator.simulate",
        entity="aggregator",
        detail={"orders": [r.order_id for r in results]},
    )
    await session.commit()
    return results
