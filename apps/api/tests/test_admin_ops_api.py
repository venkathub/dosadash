"""Admin ops integration tests: settings, kitchen pause (checkout 503),
staff role management rules, audit log listing, pubsub:settings events."""

import json

import pytest
from fakeredis import aioredis as fakeaioredis

from dosadash_api import events
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_shared import Role

ADMIN = "/api/v1/admin"


async def _login_as(db_session, phone: str, role: Role) -> tuple[dict, int]:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}, user.id


@pytest.fixture
async def admin(db_session):
    headers, _ = await _login_as(db_session, "+919555555001", Role.ADMIN)
    return headers


@pytest.fixture
async def owner(db_session):
    headers, _ = await _login_as(db_session, "+919555555002", Role.OWNER)
    return headers


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    return fake


async def _customer(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    body = (await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


# ------------------------------------------------------------------------ RBAC


async def test_settings_require_admin(client, db_session):
    assert (await client.get(f"{ADMIN}/settings")).status_code == 401
    kitchen, _ = await _login_as(db_session, "+919555555003", Role.KITCHEN_STAFF)
    assert (await client.get(f"{ADMIN}/settings", headers=kitchen)).status_code == 403
    assert (await client.get(f"{ADMIN}/audit", headers=kitchen)).status_code == 403
    assert (await client.get(f"{ADMIN}/users", headers=kitchen)).status_code == 403


# -------------------------------------------------------------------- settings


async def test_get_settings_creates_default_row(client, admin):
    resp = await client.get(f"{ADMIN}/settings", headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kitchen_paused"] is False
    assert body["delivery_pincodes"] == []


async def test_update_settings_hours_and_pincodes(client, admin):
    resp = await client.put(
        f"{ADMIN}/settings",
        headers=admin,
        json={
            "business_hours": {"mon": {"start": "08:00", "end": "22:00"}},
            "delivery_pincodes": ["600042", "600001", "600042"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["business_hours"] == {"mon": {"start": "08:00", "end": "22:00"}}
    assert body["delivery_pincodes"] == ["600001", "600042"]  # deduped + sorted

    # now address creation honours the new pincode list
    customer = await _customer(client, "9888888801")
    bad = await client.post(
        "/api/v1/addresses",
        headers=customer,
        json={"label": "Home", "line1": "1 Beach Rd", "pincode": "999999"},
    )
    assert bad.status_code == 422
    good = await client.post(
        "/api/v1/addresses",
        headers=customer,
        json={"label": "Home", "line1": "1 Beach Rd", "pincode": "600042"},
    )
    assert good.status_code == 201


async def test_update_settings_validation(client, admin):
    assert (await client.put(f"{ADMIN}/settings", headers=admin, json={})).status_code == 422
    assert (
        await client.put(
            f"{ADMIN}/settings",
            headers=admin,
            json={"business_hours": {"funday": {"start": "08:00", "end": "22:00"}}},
        )
    ).status_code == 422
    assert (
        await client.put(f"{ADMIN}/settings", headers=admin, json={"delivery_pincodes": ["60004"]})
    ).status_code == 422


# --------------------------------------------------------------- kitchen pause


async def test_kitchen_pause_blocks_checkout_and_publishes(client, admin, fake_redis):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(events.SETTINGS_CHANNEL)
    await pubsub.get_message(timeout=1)

    paused = await client.post(
        f"{ADMIN}/settings/kitchen-pause",
        headers=admin,
        json={"paused": True, "reason": "gas cylinder swap"},
    )
    assert paused.status_code == 200
    assert paused.json()["kitchen_paused"] is True

    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    body = json.loads(msg["data"])
    assert body["type"] == "settings.kitchen_pause"
    assert body["detail"] == {"paused": True, "reason": "gas cylinder swap"}

    # checkout is refused while paused
    customer = await _customer(client, "9888888802")
    menu = (await client.get("/api/v1/menu")).json()
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu[0]["id"], "qty": 1}]},
    )
    assert resp.status_code == 503
    assert "paused" in resp.json()["detail"]

    # resume → checkout works again
    await client.post(f"{ADMIN}/settings/kitchen-pause", headers=admin, json={"paused": False})
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu[0]["id"], "qty": 1}]},
    )
    assert resp.status_code == 201


# ------------------------------------------------------------------ staff RBAC


async def test_admin_can_grant_kitchen_staff(client, admin, db_session):
    _, target_id = await _login_as(db_session, "+919555555010", Role.CUSTOMER)
    resp = await client.patch(
        f"{ADMIN}/users/{target_id}/role", headers=admin, json={"role": "kitchen_staff"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "kitchen_staff"


async def test_admin_cannot_grant_or_revoke_admin(client, admin, db_session):
    _, target_id = await _login_as(db_session, "+919555555011", Role.CUSTOMER)
    resp = await client.patch(
        f"{ADMIN}/users/{target_id}/role", headers=admin, json={"role": "admin"}
    )
    assert resp.status_code == 403

    _, other_admin_id = await _login_as(db_session, "+919555555012", Role.ADMIN)
    resp = await client.patch(
        f"{ADMIN}/users/{other_admin_id}/role", headers=admin, json={"role": "customer"}
    )
    assert resp.status_code == 403


async def test_owner_can_grant_admin(client, owner, db_session):
    _, target_id = await _login_as(db_session, "+919555555013", Role.CUSTOMER)
    resp = await client.patch(
        f"{ADMIN}/users/{target_id}/role", headers=owner, json={"role": "admin"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


async def test_cannot_change_own_role(client, owner, db_session):
    me = await client.get(f"{ADMIN}/users", headers=owner, params={"role": "owner"})
    my_id = me.json()[0]["id"]
    resp = await client.patch(
        f"{ADMIN}/users/{my_id}/role", headers=owner, json={"role": "customer"}
    )
    assert resp.status_code == 403


async def test_role_change_unknown_user_404(client, owner):
    resp = await client.patch(f"{ADMIN}/users/999999/role", headers=owner, json={"role": "admin"})
    assert resp.status_code == 404


async def test_list_users_role_filter(client, admin, db_session):
    await _login_as(db_session, "+919555555014", Role.KITCHEN_STAFF)
    resp = await client.get(f"{ADMIN}/users", headers=admin, params={"role": "kitchen_staff"})
    assert resp.status_code == 200
    assert all(u["role"] == "kitchen_staff" for u in resp.json())
    assert len(resp.json()) >= 1


# ------------------------------------------------------------------- audit log


async def test_audit_log_lists_and_filters(client, admin, owner, db_session):
    # generate a few audited mutations
    await client.post(f"{ADMIN}/settings/kitchen-pause", headers=admin, json={"paused": True})
    await client.post(f"{ADMIN}/settings/kitchen-pause", headers=admin, json={"paused": False})
    _, target_id = await _login_as(db_session, "+919555555015", Role.CUSTOMER)
    await client.patch(
        f"{ADMIN}/users/{target_id}/role", headers=owner, json={"role": "kitchen_staff"}
    )

    everything = await client.get(f"{ADMIN}/audit", headers=admin)
    assert everything.status_code == 200
    actions = [r["action"] for r in everything.json()]
    assert "settings.kitchen_pause" in actions
    assert "user.role" in actions

    filtered = await client.get(
        f"{ADMIN}/audit", headers=admin, params={"action": "settings.kitchen_pause"}
    )
    rows = filtered.json()
    assert len(rows) == 2
    assert rows[0]["detail"]["paused"] is False  # newest first
    assert rows[1]["detail"]["paused"] is True

    limited = await client.get(f"{ADMIN}/audit", headers=admin, params={"limit": 1})
    assert len(limited.json()) == 1


# ------------------------------------------------------------- event payloads


def test_settings_event_payload_shape():
    payload = events.settings_event_payload("settings.updated", detail={"fields": ["x"]})
    assert payload == {"type": "settings.updated", "detail": {"fields": ["x"]}}


async def test_settings_publish_best_effort(monkeypatch):
    class Exploding:
        async def publish(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(events, "get_redis", lambda: Exploding())
    await events.publish_settings_event("settings.updated")  # must not raise
