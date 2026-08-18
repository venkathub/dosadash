"""Phase 6 inventory foundation tests: supplier CRUD (+ ingredient linking),
wastage log (stock decrement, clamp-at-zero, audit), inventory events."""

import json
from decimal import Decimal

import pytest
from fakeredis import aioredis as fakeaioredis
from sqlalchemy import select

from dosadash_api import events
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Ingredient, StaffAction, User
from dosadash_shared import Role

SUPPLIERS = "/api/v1/admin/suppliers"
WASTAGE = "/api/v1/admin/wastage"
INGREDIENTS = "/api/v1/admin/ingredients"


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
    return await _login_as(db_session, "+919555558001", Role.ADMIN)


@pytest.fixture
async def kitchen(db_session):
    return await _login_as(db_session, "+919555558002", Role.KITCHEN_STAFF)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    return fake


async def _make_supplier(client, admin, name="Chennai Fresh Produce", **kw) -> dict:
    resp = await client.post(SUPPLIERS, headers=admin, json={"name": name, **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _ingredient_id(client, admin, name="idli rice") -> int:
    rows = (await client.get(INGREDIENTS, headers=admin)).json()
    return next(i["id"] for i in rows if i["name"] == name)


# ------------------------------------------------------------------------ RBAC


async def test_inventory_rbac(client, db_session, kitchen):
    assert (await client.get(SUPPLIERS)).status_code == 401
    assert (await client.get(WASTAGE)).status_code == 401
    # kitchen staff may log wastage but not manage suppliers
    assert (await client.get(SUPPLIERS, headers=kitchen)).status_code == 403
    assert (await client.get(WASTAGE, headers=kitchen)).status_code == 200
    customer = await _login_as(db_session, "+919555558003", Role.CUSTOMER)
    assert (await client.get(WASTAGE, headers=customer)).status_code == 403


# ------------------------------------------------------------------- suppliers


async def test_supplier_crud(client, admin):
    supplier = await _make_supplier(client, admin, lead_time_days=2)
    assert supplier["lead_time_days"] == 2
    assert supplier["is_active"] is True

    dup = await client.post(SUPPLIERS, headers=admin, json={"name": supplier["name"]})
    assert dup.status_code == 409

    patched = await client.patch(
        f"{SUPPLIERS}/{supplier['id']}", headers=admin, json={"phone": "+919000000001"}
    )
    assert patched.status_code == 200
    assert patched.json()["phone"] == "+919000000001"

    listed = (await client.get(SUPPLIERS, headers=admin)).json()
    assert [s["name"] for s in listed] == [supplier["name"]]

    assert (await client.delete(f"{SUPPLIERS}/{supplier['id']}", headers=admin)).status_code == 204
    assert (await client.get(SUPPLIERS, headers=admin)).json() == []


async def test_supplier_delete_refused_when_linked(client, admin, db_session):
    supplier = await _make_supplier(client, admin)
    rice_id = await _ingredient_id(client, admin)
    resp = await client.patch(
        f"{INGREDIENTS}/{rice_id}", headers=admin, json={"supplier_id": supplier["id"]}
    )
    assert resp.status_code == 200
    assert resp.json()["supplier_id"] == supplier["id"]

    refused = await client.delete(f"{SUPPLIERS}/{supplier['id']}", headers=admin)
    assert refused.status_code == 409

    # deactivate instead is allowed
    deact = await client.patch(
        f"{SUPPLIERS}/{supplier['id']}", headers=admin, json={"is_active": False}
    )
    assert deact.status_code == 200
    assert deact.json()["is_active"] is False


async def test_ingredient_link_to_unknown_supplier_rejected(client, admin):
    rice_id = await _ingredient_id(client, admin)
    resp = await client.patch(f"{INGREDIENTS}/{rice_id}", headers=admin, json={"supplier_id": 9999})
    assert resp.status_code == 422


# ----------------------------------------------------------------- wastage log


async def _set_stock(db_session, ingredient_id: int, qty: str) -> None:
    ingredient = await db_session.get(Ingredient, ingredient_id)
    ingredient.stock_qty = Decimal(qty)
    await db_session.commit()


async def test_wastage_decrements_stock_and_audits(client, admin, kitchen, db_session, fake_redis):
    rice_id = await _ingredient_id(client, admin)
    await _set_stock(db_session, rice_id, "10.000")

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(events.INVENTORY_CHANNEL)
    await pubsub.get_message(timeout=1)

    resp = await client.post(
        WASTAGE,
        headers=kitchen,
        json={"ingredient_id": rice_id, "qty": "2.5", "reason": "SPOILAGE", "note": "rain leak"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ingredient_name"] == "idli rice"
    assert Decimal(body["stock_after"]) == Decimal("7.5")

    ingredient = await db_session.get(Ingredient, rice_id)
    await db_session.refresh(ingredient)
    assert ingredient.stock_qty == Decimal("7.5")

    action = await db_session.scalar(select(StaffAction).where(StaffAction.action == "wastage.log"))
    assert action is not None
    assert action.detail["clamped"] is False

    msg = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert msg["type"] == "inventory.wastage"
    assert msg["detail"]["ingredient_id"] == rice_id

    listed = (await client.get(WASTAGE, headers=admin, params={"ingredient_id": rice_id})).json()
    assert len(listed) == 1
    assert listed[0]["reason"] == "SPOILAGE"


async def test_wastage_clamps_stock_at_zero(client, admin, db_session):
    rice_id = await _ingredient_id(client, admin)
    await _set_stock(db_session, rice_id, "1.000")

    resp = await client.post(
        WASTAGE, headers=admin, json={"ingredient_id": rice_id, "qty": "5", "reason": "EXPIRED"}
    )
    assert resp.status_code == 201
    assert Decimal(resp.json()["stock_after"]) == Decimal("0")

    action = await db_session.scalar(select(StaffAction).where(StaffAction.action == "wastage.log"))
    assert action.detail["clamped"] is True


async def test_wastage_validation(client, admin):
    rice_id = await _ingredient_id(client, admin)
    assert (
        await client.post(
            WASTAGE, headers=admin, json={"ingredient_id": 9999, "qty": "1", "reason": "OTHER"}
        )
    ).status_code == 404
    assert (
        await client.post(
            WASTAGE, headers=admin, json={"ingredient_id": rice_id, "qty": "0", "reason": "OTHER"}
        )
    ).status_code == 422
    assert (
        await client.post(
            WASTAGE, headers=admin, json={"ingredient_id": rice_id, "qty": "1", "reason": "VIBES"}
        )
    ).status_code == 422
