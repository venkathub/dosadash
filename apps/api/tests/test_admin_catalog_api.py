"""Admin catalog tests: combo builder + approval flow, ingredient CRUD,
recipe mapping (allergen source of truth), catalog events."""

import json

import pytest
from fakeredis import aioredis as fakeaioredis
from sqlalchemy import select

from dosadash_api import events
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import StaffAction, User
from dosadash_shared import Role

COMBOS = "/api/v1/admin/combos"
INGREDIENTS = "/api/v1/admin/ingredients"
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
    return await _login_as(db_session, "+919555557001", Role.ADMIN)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    return fake


async def _menu(client) -> dict[str, dict]:
    return {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}


async def _make_combo(client, admin, name="Coffee Combo", price="150.00") -> dict:
    menu = await _menu(client)
    resp = await client.post(
        COMBOS,
        headers=admin,
        json={
            "name": name,
            "item_ids": [menu["Masala Dosa"]["id"], menu["Filter Coffee"]["id"]],
            "price": price,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------------ RBAC


async def test_catalog_rbac(client, db_session):
    assert (await client.get(COMBOS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555557002", Role.KITCHEN_STAFF)
    assert (await client.get(COMBOS, headers=kitchen)).status_code == 403
    assert (await client.get(INGREDIENTS, headers=kitchen)).status_code == 403


# ---------------------------------------------------------------------- combos


async def test_combo_create_starts_draft_and_hidden(client, admin):
    combo = await _make_combo(client, admin)
    assert combo["status"] == "DRAFT"
    assert combo["source"] == "MANUAL"
    public = await client.get("/api/v1/menu/combos")
    assert public.json() == []


async def test_combo_validation(client, admin):
    menu = await _menu(client)
    ids = [menu["Masala Dosa"]["id"], menu["Filter Coffee"]["id"]]
    # unknown item
    resp = await client.post(
        COMBOS, headers=admin, json={"name": "Ghost", "item_ids": [999999, ids[0]], "price": "100"}
    )
    assert resp.status_code == 404
    # price above sum of parts (120 + 60 = 180)
    resp = await client.post(
        COMBOS, headers=admin, json={"name": "Ripoff", "item_ids": ids, "price": "200"}
    )
    assert resp.status_code == 422
    # fewer than 2 items
    resp = await client.post(
        COMBOS, headers=admin, json={"name": "Solo", "item_ids": [ids[0]], "price": "100"}
    )
    assert resp.status_code == 422


async def test_combo_approval_flow(client, admin, db_session):
    combo = await _make_combo(client, admin)

    approved = await client.post(
        f"{COMBOS}/{combo['id']}/status", headers=admin, json={"status": "APPROVED"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    public = await client.get("/api/v1/menu/combos")
    assert [c["name"] for c in public.json()] == ["Coffee Combo"]

    # idempotent approve refused
    again = await client.post(
        f"{COMBOS}/{combo['id']}/status", headers=admin, json={"status": "APPROVED"}
    )
    assert again.status_code == 409

    # pull it back
    rejected = await client.post(
        f"{COMBOS}/{combo['id']}/status", headers=admin, json={"status": "REJECTED"}
    )
    assert rejected.json()["status"] == "REJECTED"
    assert (await client.get("/api/v1/menu/combos")).json() == []

    audit_rows = await db_session.scalars(
        select(StaffAction).where(StaffAction.action == "combo.status")
    )
    assert len(list(audit_rows)) == 2


async def test_combo_approve_revalidates_items(client, admin):
    """An item 86'd... still fine (86 is temporary) — but a *deleted* item
    must block approval. We validate existence at approval time."""
    combo = await _make_combo(client, admin, name="Doomed Combo")
    menu = await _menu(client)
    coffee_id = menu["Filter Coffee"]["id"]
    # make coffee cheaper so the combo price now exceeds the parts sum
    await client.patch(f"{ADMIN_MENU}/items/{coffee_id}", headers=admin, json={"price": "20.00"})
    resp = await client.post(
        f"{COMBOS}/{combo['id']}/status", headers=admin, json={"status": "APPROVED"}
    )
    assert resp.status_code == 422  # 150 > 120 + 20


async def test_combo_update_and_delete(client, admin):
    combo = await _make_combo(client, admin)
    updated = await client.patch(f"{COMBOS}/{combo['id']}", headers=admin, json={"price": "140.00"})
    assert updated.status_code == 200
    assert updated.json()["price"] == "140.00"

    bad = await client.patch(f"{COMBOS}/{combo['id']}", headers=admin, json={"price": "999.00"})
    assert bad.status_code == 422

    assert (await client.delete(f"{COMBOS}/{combo['id']}", headers=admin)).status_code == 204
    assert (await client.get(COMBOS, headers=admin)).json() == []


# ------------------------------------------------------------------ ingredients


async def test_ingredient_crud(client, admin):
    listing = await client.get(INGREDIENTS, headers=admin)
    assert listing.status_code == 200
    names = [i["name"] for i in listing.json()]
    assert "peanut" in names  # seeded

    created = await client.post(
        INGREDIENTS,
        headers=admin,
        json={"name": "urad dal", "unit": "kg", "cost": "180.00"},
    )
    assert created.status_code == 201
    assert created.json()["is_allergen"] is False

    dup = await client.post(INGREDIENTS, headers=admin, json={"name": "urad dal", "unit": "kg"})
    assert dup.status_code == 409

    patched = await client.patch(
        f"{INGREDIENTS}/{created.json()['id']}",
        headers=admin,
        json={"reorder_point": "5.000"},
    )
    assert patched.status_code == 200
    assert patched.json()["reorder_point"] == "5.000"


async def test_ingredient_delete_guard(client, admin):
    # peanut is used by Lemon Rice → refuse delete
    listing = (await client.get(INGREDIENTS, headers=admin)).json()
    peanut = next(i for i in listing if i["name"] == "peanut")
    resp = await client.delete(f"{INGREDIENTS}/{peanut['id']}", headers=admin)
    assert resp.status_code == 409

    # an unused ingredient deletes fine
    created = await client.post(INGREDIENTS, headers=admin, json={"name": "jaggery", "unit": "kg"})
    assert (
        await client.delete(f"{INGREDIENTS}/{created.json()['id']}", headers=admin)
    ).status_code == 204


async def test_allergen_flip_changes_public_badges(client, admin):
    """is_allergen drives the public allergen badges — the whole point of
    the single source of truth."""
    listing = (await client.get(INGREDIENTS, headers=admin)).json()
    rice = next(i for i in listing if i["name"] == "idli rice")
    await client.patch(f"{INGREDIENTS}/{rice['id']}", headers=admin, json={"is_allergen": True})

    masala = (await client.get("/api/v1/menu", params={"q": "masala"})).json()[0]
    assert "idli rice" in masala["allergens"]


# ---------------------------------------------------------------- recipe mapping


async def test_recipe_get_and_replace(client, admin, db_session):
    menu = await _menu(client)
    dosa_id = menu["Masala Dosa"]["id"]

    current = await client.get(f"{ADMIN_MENU}/items/{dosa_id}/recipe", headers=admin)
    assert current.status_code == 200
    assert [line["name"] for line in current.json()] == ["idli rice"]

    ingredients = {i["name"]: i for i in (await client.get(INGREDIENTS, headers=admin)).json()}
    resp = await client.put(
        f"{ADMIN_MENU}/items/{dosa_id}/recipe",
        headers=admin,
        json={
            "lines": [
                {"ingredient_id": ingredients["idli rice"]["id"], "qty": "0.200"},
                {"ingredient_id": ingredients["milk"]["id"], "qty": "0.050"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [line["name"] for line in body] == ["idli rice", "milk"]
    assert next(line for line in body if line["name"] == "milk")["is_allergen"] is True

    # public detail reflects the new allergen immediately
    detail = (await client.get(f"/api/v1/menu/items/{dosa_id}")).json()
    assert detail["allergens"] == ["milk"]
    assert detail["ingredients"] == ["idli rice", "milk"]


async def test_recipe_validation(client, admin):
    menu = await _menu(client)
    dosa_id = menu["Masala Dosa"]["id"]
    resp = await client.put(
        f"{ADMIN_MENU}/items/{dosa_id}/recipe",
        headers=admin,
        json={"lines": [{"ingredient_id": 999999, "qty": "1"}]},
    )
    assert resp.status_code == 404

    ingredients = (await client.get(INGREDIENTS, headers=admin)).json()
    iid = ingredients[0]["id"]
    dup = await client.put(
        f"{ADMIN_MENU}/items/{dosa_id}/recipe",
        headers=admin,
        json={"lines": [{"ingredient_id": iid, "qty": "1"}, {"ingredient_id": iid, "qty": "2"}]},
    )
    assert dup.status_code == 422


# -------------------------------------------------------------- event cascade


async def test_catalog_and_recipe_events(client, admin, fake_redis):
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(events.MENU_CHANNEL)
    await pubsub.get_message(timeout=1)

    combo = await _make_combo(client, admin)
    msg = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert msg["type"] == "combo.created"
    assert msg["detail"]["combo_id"] == combo["id"]

    await client.post(f"{COMBOS}/{combo['id']}/status", headers=admin, json={"status": "APPROVED"})
    msg = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert msg["type"] == "combo.status"
    assert msg["detail"]["status"] == "APPROVED"

    menu = await _menu(client)
    ingredients = (await client.get(INGREDIENTS, headers=admin)).json()
    await client.put(
        f"{ADMIN_MENU}/items/{menu['Masala Dosa']['id']}/recipe",
        headers=admin,
        json={"lines": [{"ingredient_id": ingredients[0]["id"], "qty": "1"}]},
    )
    msg = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert msg["type"] == "menu.recipe"
    assert msg["item_id"] == menu["Masala Dosa"]["id"]
