"""Admin menu ops integration tests: RBAC, CRUD, 86 toggle, schedule,
customizations, audit rows, and pubsub:menu event cascade."""

import json

import pytest
from fakeredis import aioredis as fakeaioredis
from sqlalchemy import func, select

from dosadash_api import events
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import MenuItem, StaffAction, User
from dosadash_shared import Role

ADMIN_MENU = "/api/v1/admin/menu"


async def _login_as(db_session, phone: str, role: Role) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin(db_session):
    return await _login_as(db_session, "+919555555555", Role.ADMIN)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    return fake


async def _item_id(client, admin, name: str) -> int:
    resp = await client.get(f"{ADMIN_MENU}/items", headers=admin)
    return next(i["id"] for i in resp.json() if i["name"] == name)


# ------------------------------------------------------------------------ RBAC


async def test_admin_routes_require_auth(client):
    assert (await client.get(f"{ADMIN_MENU}/items")).status_code == 401


async def test_admin_routes_reject_customer_and_kitchen(client, db_session):
    customer = await _login_as(db_session, "+919666666601", Role.CUSTOMER)
    kitchen = await _login_as(db_session, "+919666666602", Role.KITCHEN_STAFF)
    for headers in (customer, kitchen):
        assert (await client.get(f"{ADMIN_MENU}/items", headers=headers)).status_code == 403
        resp = await client.post(
            f"{ADMIN_MENU}/items",
            headers=headers,
            json={"name": "Nope Dosa", "category": "Dosa", "price": "99"},
        )
        assert resp.status_code == 403


async def test_owner_allowed(client, db_session):
    owner = await _login_as(db_session, "+919666666603", Role.OWNER)
    assert (await client.get(f"{ADMIN_MENU}/items", headers=owner)).status_code == 200


# ------------------------------------------------------------------------ CRUD


async def test_admin_list_includes_86d_items(client, admin):
    resp = await client.get(f"{ADMIN_MENU}/items", headers=admin)
    names = [i["name"] for i in resp.json()]
    assert "Seasonal Special" in names  # hidden publicly, visible to admin
    assert len(names) == 5


async def test_create_item_appears_in_public_menu(client, admin):
    resp = await client.post(
        f"{ADMIN_MENU}/items",
        headers=admin,
        json={
            "name": "Podi Idli",
            "category": "Idli & Vada",
            "price": "90.00",
            "description": "Mini idlis tossed in gunpowder",
            "spice_level": 2,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_available"] is True
    assert body["gst_rate"] == "5.00"  # sensible default

    public = await client.get("/api/v1/menu", params={"q": "podi"})
    assert [i["name"] for i in public.json()] == ["Podi Idli"]


async def test_create_duplicate_name_409(client, admin):
    payload = {"name": "Masala Dosa", "category": "Dosa", "price": "120"}
    resp = await client.post(f"{ADMIN_MENU}/items", headers=admin, json=payload)
    assert resp.status_code == 409


async def test_update_item_price(client, admin):
    item_id = await _item_id(client, admin, "Filter Coffee")
    resp = await client.patch(
        f"{ADMIN_MENU}/items/{item_id}", headers=admin, json={"price": "70.00"}
    )
    assert resp.status_code == 200
    assert resp.json()["price"] == "70.00"

    public = await client.get(f"/api/v1/menu/items/{item_id}")
    assert public.json()["price"] == "70.00"


async def test_update_empty_body_422(client, admin):
    item_id = await _item_id(client, admin, "Filter Coffee")
    resp = await client.patch(f"{ADMIN_MENU}/items/{item_id}", headers=admin, json={})
    assert resp.status_code == 422


async def test_update_missing_item_404(client, admin):
    resp = await client.patch(f"{ADMIN_MENU}/items/999999", headers=admin, json={"price": "10"})
    assert resp.status_code == 404


async def test_delete_unreferenced_item(client, admin):
    item_id = await _item_id(client, admin, "Seasonal Special")
    assert (await client.delete(f"{ADMIN_MENU}/items/{item_id}", headers=admin)).status_code == 204
    assert (await client.get(f"/api/v1/menu/items/{item_id}")).status_code == 404


async def test_delete_ordered_item_409(client, admin, db_session):
    # place an order referencing Masala Dosa, then try to delete it
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9777777777"})
    otp = req.json()["demo_otp"]
    tokens = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9777777777", "otp": otp})
    ).json()
    customer = {"Authorization": f"Bearer {tokens['access_token']}"}
    item_id = await _item_id(client, admin, "Masala Dosa")
    order = await client.post(
        "/api/v1/orders", headers=customer, json={"items": [{"item_id": item_id, "qty": 1}]}
    )
    assert order.status_code == 201

    resp = await client.delete(f"{ADMIN_MENU}/items/{item_id}", headers=admin)
    assert resp.status_code == 409
    assert "86" in resp.json()["detail"]
    # item survives the failed delete
    assert (await client.get(f"/api/v1/menu/items/{item_id}")).status_code == 200


# ------------------------------------------------------------------ 86 toggle


async def test_86_toggle_hides_and_restores_with_audit(client, admin, db_session):
    item_id = await _item_id(client, admin, "Masala Dosa")

    off = await client.post(
        f"{ADMIN_MENU}/items/{item_id}/availability",
        headers=admin,
        json={"is_available": False},
    )
    assert off.status_code == 200
    assert off.json()["is_available"] is False
    names = [i["name"] for i in (await client.get("/api/v1/menu")).json()]
    assert "Masala Dosa" not in names

    on = await client.post(
        f"{ADMIN_MENU}/items/{item_id}/availability",
        headers=admin,
        json={"is_available": True},
    )
    assert on.json()["is_available"] is True
    names = [i["name"] for i in (await client.get("/api/v1/menu")).json()]
    assert "Masala Dosa" in names

    audit_count = await db_session.scalar(
        select(func.count()).select_from(StaffAction).where(StaffAction.action == "menu.86")
    )
    assert audit_count == 2


# ------------------------------------------------------------------- schedule


async def test_set_and_clear_schedule(client, admin, db_session):
    item_id = await _item_id(client, admin, "Chicken Biryani")
    window = {"start": "11:00", "end": "22:00"}
    schedule = {"sat": window, "sun": window}
    resp = await client.put(
        f"{ADMIN_MENU}/items/{item_id}/schedule", headers=admin, json={"schedule": schedule}
    )
    assert resp.status_code == 200
    assert resp.json()["schedule"] == schedule
    stored = await db_session.scalar(select(MenuItem.schedule).where(MenuItem.id == item_id))
    assert stored == schedule

    cleared = await client.put(
        f"{ADMIN_MENU}/items/{item_id}/schedule", headers=admin, json={"schedule": None}
    )
    assert cleared.json()["schedule"] is None


async def test_schedule_rejects_bad_day_and_time(client, admin):
    item_id = await _item_id(client, admin, "Chicken Biryani")
    bad_day = await client.put(
        f"{ADMIN_MENU}/items/{item_id}/schedule",
        headers=admin,
        json={"schedule": {"funday": {"start": "11:00", "end": "22:00"}}},
    )
    assert bad_day.status_code == 422
    bad_time = await client.put(
        f"{ADMIN_MENU}/items/{item_id}/schedule",
        headers=admin,
        json={"schedule": {"mon": {"start": "25:00", "end": "22:00"}}},
    )
    assert bad_time.status_code == 422


# ------------------------------------------------------------- customizations


async def test_customization_add_and_delete(client, admin):
    item_id = await _item_id(client, admin, "Filter Coffee")
    added = await client.post(
        f"{ADMIN_MENU}/items/{item_id}/customizations",
        headers=admin,
        json={"name": "Extra strong decoction", "price_delta": "10.00"},
    )
    assert added.status_code == 201
    cust_id = added.json()["id"]

    detail = await client.get(f"/api/v1/menu/items/{item_id}")
    assert "Extra strong decoction" in [c["name"] for c in detail.json()["customizations"]]

    assert (
        await client.delete(f"{ADMIN_MENU}/customizations/{cust_id}", headers=admin)
    ).status_code == 204
    assert (
        await client.delete(f"{ADMIN_MENU}/customizations/{cust_id}", headers=admin)
    ).status_code == 404


# -------------------------------------------------------------- event cascade


def test_menu_event_payload_shape():
    payload = events.menu_event_payload("menu.availability", item_id=7, detail={"x": 1})
    assert payload == {"type": "menu.availability", "item_id": 7, "detail": {"x": 1}}


async def test_menu_event_publish_best_effort(monkeypatch):
    class Exploding:
        async def publish(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(events, "get_redis", lambda: Exploding())
    await events.publish_menu_event("menu.updated", item_id=1)  # must not raise


async def test_mutations_publish_menu_events(client, admin, fake_redis):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(events.MENU_CHANNEL)
    await pubsub.get_message(timeout=1)  # consume subscribe confirmation

    item_id = await _item_id(client, admin, "Masala Dosa")
    await client.post(
        f"{ADMIN_MENU}/items/{item_id}/availability",
        headers=admin,
        json={"is_available": False},
    )
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert msg is not None
    body = json.loads(msg["data"])
    assert body["type"] == "menu.availability"
    assert body["item_id"] == item_id
    assert body["detail"] == {"name": "Masala Dosa", "is_available": False}

    await client.patch(f"{ADMIN_MENU}/items/{item_id}", headers=admin, json={"price": "130"})
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    body = json.loads(msg["data"])
    assert body["type"] == "menu.updated"
    assert body["detail"] == {"fields": ["price"]}
