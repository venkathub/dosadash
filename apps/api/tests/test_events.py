"""Event cascade tests: payload shape + publish-on-mutation via fakeredis."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fakeredis import aioredis as fakeaioredis

from dosadash_api import events
from dosadash_api.db.models import MenuItem, Order, OrderItem
from dosadash_api.routers.ws import _authenticate
from dosadash_shared import ChannelType, OrderState


def _order_in_memory() -> Order:
    item = MenuItem(
        id=1,
        brand_id=1,
        name="Masala Dosa",
        price=Decimal("120"),
        category="Dosa",
        description="",
    )
    order = Order(
        id=99,
        user_id=7,
        brand_id=1,
        channel=ChannelType.WEB,
        status=OrderState.PLACED,
        subtotal=Decimal("240"),
        gst=Decimal("12"),
        total=Decimal("252.00"),
        placed_at=datetime(2026, 8, 16, 12, 30, tzinfo=UTC),
    )
    order.items = [OrderItem(item=item, qty=2, unit_price=Decimal("120"))]
    return order


def test_order_event_payload_shape():
    payload = events.order_event_payload("order.created", _order_in_memory())
    assert payload == {
        "type": "order.created",
        "order_id": 99,
        "status": "PLACED",
        "user_id": 7,
        "total": "252.00",
        "channel": "WEB",
        "placed_at": "2026-08-16T12:30:00+00:00",
        "items": [{"name": "Masala Dosa", "qty": 2}],
    }


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    return fake


async def test_publish_is_best_effort_on_redis_failure(monkeypatch):
    class Exploding:
        async def publish(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(events, "get_redis", lambda: Exploding())
    # must not raise — checkout survives a Redis outage
    await events.publish_order_event("order.created", _order_in_memory())


async def test_checkout_and_transition_publish_events(client, db_session, fake_redis):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(events.ORDERS_CHANNEL)
    await pubsub.get_message(timeout=1)  # consume subscribe confirmation

    # login + checkout (reuses REST flow)
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9444444444"})
    otp = req.json()["demo_otp"]
    tokens = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9444444444", "otp": otp})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    menu = (await client.get("/api/v1/menu")).json()
    item_id = menu[0]["id"]
    order = (
        await client.post(
            "/api/v1/orders", headers=headers, json={"items": [{"item_id": item_id, "qty": 1}]}
        )
    ).json()

    created = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert created is not None
    body = json.loads(created["data"])
    assert body["type"] == "order.created"
    assert body["order_id"] == order["id"]
    assert body["status"] == "PLACED"

    # customer cancel → order.status event
    await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=headers)
    status_event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert status_event is not None
    body = json.loads(status_event["data"])
    assert body["type"] == "order.status"
    assert body["status"] == "CANCELLED"


async def test_ws_authenticate_rejects_garbage(db_session):
    assert await _authenticate("garbage-token") is None
