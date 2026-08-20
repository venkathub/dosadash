"""Mock-aggregator channel tests (Phase 7, docs/04 O12): signed webhook →
same order pipeline, prepaid settlement, idempotent retries, status poll."""

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from dosadash_api import config
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import (
    AggregatorOrder,
    MenuItem,
    Order,
    Payment,
    StaffAction,
    User,
)
from dosadash_shared import ChannelType, PaymentStatus, Role

WEBHOOK = "/api/v1/aggregator/webhook"
SECRET = "test-aggregator-secret"


def _sign(payload: bytes) -> str:
    return hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()


def _payload(**overrides) -> dict:
    base = {
        "aggregator": "mockswiggy",
        "external_order_id": "SWG-1001",
        "customer": {"name": "Priya", "phone": "+919812345678"},
        "items": [
            {"name": "Masala Dosa", "qty": 2},
            {"name": "Filter Coffee", "qty": 1},
        ],
    }
    base.update(overrides)
    return base


async def _post(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    return await client.post(
        WEBHOOK,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Aggregator-Signature": signature if signature is not None else _sign(body),
        },
    )


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("API_AGGREGATOR_WEBHOOK_SECRET", SECRET)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


async def _admin(db_session) -> dict:
    user = User(phone="+919555559301", name="admin user", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def test_unconfigured_channel_is_503(client, monkeypatch):
    monkeypatch.delenv("API_AGGREGATOR_WEBHOOK_SECRET", raising=False)
    config.get_settings.cache_clear()
    resp = await _post(client, _payload())
    assert resp.status_code == 503


async def test_bad_signature_is_403(client, db_session):
    assert (await _post(client, _payload(), signature="deadbeef")).status_code == 403
    assert (await _post(client, _payload(), signature="")).status_code == 403


async def test_webhook_order_rides_the_same_pipeline(client, db_session):
    resp = await _post(client, _payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PLACED"
    assert body["duplicate"] is False

    order = await db_session.get(Order, body["order_id"])
    assert order.channel == ChannelType.MOCK_AGGREGATOR
    assert order.subtotal > 0  # totals computed server-side, not trusted

    # prepaid: settled immediately through the provider interface
    payment = await db_session.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.provider == "aggregator"

    # aggregator customer upserted, labeled with the channel
    user = await db_session.get(User, order.user_id)
    assert user.phone == "+919812345678"
    assert "(via mockswiggy)" in user.name

    # mapping recorded for idempotency + status polling
    mapping = await db_session.scalar(
        select(AggregatorOrder).where(AggregatorOrder.order_id == order.id)
    )
    assert mapping.external_order_id == "SWG-1001"


async def test_webhook_retry_is_idempotent(client, db_session):
    first = (await _post(client, _payload())).json()
    retry = await _post(client, _payload())
    assert retry.status_code == 201
    assert retry.json()["order_id"] == first["order_id"]
    assert retry.json()["duplicate"] is True
    assert await db_session.scalar(select(Order).where(Order.id == first["order_id"])) is not None
    count = len((await db_session.scalars(select(AggregatorOrder))).all())
    assert count == 1  # no second order, no second mapping


async def test_unknown_items_refused_loudly(client, db_session):
    resp = await _post(client, _payload(items=[{"name": "Paneer Tikka Pizza", "qty": 1}]))
    assert resp.status_code == 422
    assert "paneer tikka pizza" in resp.json()["detail"]
    assert (await db_session.scalar(select(AggregatorOrder))) is None  # nothing persisted


async def test_86d_item_conflicts(client, db_session):
    special = await db_session.scalar(select(MenuItem).where(MenuItem.name == "Seasonal Special"))
    assert special.is_available is False  # seeded 86'd
    resp = await _post(client, _payload(items=[{"name": "Seasonal Special", "qty": 1}]))
    assert resp.status_code == 409


async def test_unknown_aggregator_is_422(client):
    assert (await _post(client, _payload(aggregator="ubereats"))).status_code == 422


async def test_status_poll_with_path_signature(client, db_session):
    created = (await _post(client, _payload())).json()
    sig = _sign(b"mockswiggy|SWG-1001")
    resp = await client.get(
        "/api/v1/aggregator/orders/mockswiggy/SWG-1001",
        headers={"X-Aggregator-Signature": sig},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == created["order_id"]
    assert body["status"] == "PLACED"

    # wrong signature / unknown order
    resp = await client.get(
        "/api/v1/aggregator/orders/mockswiggy/SWG-1001",
        headers={"X-Aggregator-Signature": "nope"},
    )
    assert resp.status_code == 403
    resp = await client.get(
        "/api/v1/aggregator/orders/mockswiggy/SWG-9999",
        headers={"X-Aggregator-Signature": _sign(b"mockswiggy|SWG-9999")},
    )
    assert resp.status_code == 404


async def test_simulate_is_rbac_gated_and_audited(client, db_session):
    assert (
        await client.post("/api/v1/admin/aggregator/simulate", json={"count": 1})
    ).status_code == 401
    admin = await _admin(db_session)
    resp = await client.post("/api/v1/admin/aggregator/simulate", headers=admin, json={"count": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    for entry in body:
        order = await db_session.get(Order, entry["order_id"])
        assert order.channel == ChannelType.MOCK_AGGREGATOR
    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "aggregator.simulate")
    )
    assert sorted(action.detail["orders"]) == sorted(e["order_id"] for e in body)
