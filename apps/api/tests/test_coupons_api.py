"""Coupon API tests: preview, checkout redemption, admin CRUD + guardrails."""

from decimal import Decimal

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Coupon, User
from dosadash_shared import CouponType, Role


async def _customer(client, phone="9111113301") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _admin(db_session, phone="+919333330071") -> dict:
    user = User(phone=phone, name="Coupon Admin", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _menu(client) -> dict[str, dict]:
    return {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}


async def _active_coupon(db_session, **overrides) -> Coupon:
    defaults = dict(
        code="DOSA10",
        type=CouponType.PCT,
        value=Decimal("10"),
        max_discount=Decimal("40"),
        is_active=True,
    )
    defaults.update(overrides)
    coupon = Coupon(**defaults)
    db_session.add(coupon)
    await db_session.commit()
    return coupon


@pytest.fixture
async def customer(client):
    return await _customer(client)


# -------------------------------------------------------------------- preview


async def test_preview_prices_cart(client, customer, db_session):
    await _active_coupon(db_session)
    menu = await _menu(client)
    resp = await client.post(
        "/api/v1/coupons/preview",
        json={"code": "dosa10", "items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 2}]},
        headers=customer,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "DOSA10"
    subtotal = Decimal(body["subtotal"])
    assert Decimal(body["discount"]) == (subtotal / 10).quantize(Decimal("0.01"))
    assert Decimal(body["total"]) == subtotal - Decimal(body["discount"]) + Decimal(body["gst"])


async def test_preview_invalid_code_400(client, customer):
    menu = await _menu(client)
    resp = await client.post(
        "/api/v1/coupons/preview",
        json={"code": "NOPE", "items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
        headers=customer,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid coupon code"


async def test_preview_requires_auth(client):
    resp = await client.post(
        "/api/v1/coupons/preview", json={"code": "X2", "items": [{"item_id": 1, "qty": 1}]}
    )
    assert resp.status_code in (401, 403)


# ------------------------------------------------------------------- checkout


async def test_checkout_with_coupon_discounts_and_redeems(client, customer, db_session):
    await _active_coupon(
        db_session,
        code="FLAT50",
        type=CouponType.FLAT,
        value=Decimal("50"),
        max_discount=None,
        per_user_limit=1,
    )
    menu = await _menu(client)
    body = {
        "items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 3}],
        "coupon_code": "flat50",
    }
    resp = await client.post("/api/v1/orders", json=body, headers=customer)
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert Decimal(order["discount"]) == Decimal("50.00")
    assert order["coupon_code"] == "FLAT50"
    expected_total = Decimal(order["subtotal"]) - Decimal("50.00") + Decimal(order["gst"])
    assert Decimal(order["total"]) == expected_total
    # GST must be charged on the discounted amount (less than 5% of subtotal)
    assert Decimal(order["gst"]) < Decimal(order["subtotal"]) * Decimal("0.05")

    # per_user_limit=1 → second use rejected with a 400, order NOT created
    resp2 = await client.post("/api/v1/orders", json=body, headers=customer)
    assert resp2.status_code == 400
    assert "already used" in resp2.json()["detail"]


async def test_checkout_without_coupon_unchanged(client, customer):
    menu = await _menu(client)
    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"item_id": menu["Filter Coffee"]["id"], "qty": 1}]},
        headers=customer,
    )
    assert resp.status_code == 201
    order = resp.json()
    assert Decimal(order["discount"]) == Decimal("0")
    assert order["coupon_code"] is None


async def test_checkout_bad_coupon_400_no_order(client, customer):
    menu = await _menu(client)
    resp = await client.post(
        "/api/v1/orders",
        json={
            "items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}],
            "coupon_code": "GHOST",
        },
        headers=customer,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid coupon code"


# ----------------------------------------------------------------- admin CRUD


async def test_admin_create_activate_flow(client, db_session):
    admin = await _admin(db_session)
    resp = await client.post(
        "/api/v1/admin/coupons",
        json={"code": "welcome20", "type": "PCT", "value": "20", "max_discount": "60"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["code"] == "WELCOME20"  # normalized
    assert body["is_active"] is False  # born inactive
    coupon_id = body["id"]

    resp = await client.patch(
        f"/api/v1/admin/coupons/{coupon_id}", json={"is_active": True}, headers=admin
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    listed = await client.get("/api/v1/admin/coupons?active=true", headers=admin)
    assert any(c["code"] == "WELCOME20" for c in listed.json())


async def test_admin_guardrails(client, db_session):
    admin = await _admin(db_session, phone="+919333330072")
    # PCT over 50% rejected
    resp = await client.post(
        "/api/v1/admin/coupons",
        json={"code": "TOOBIG", "type": "PCT", "value": "80", "max_discount": "500"},
        headers=admin,
    )
    assert resp.status_code == 422
    # PCT without max_discount rejected
    resp = await client.post(
        "/api/v1/admin/coupons",
        json={"code": "NOCAP", "type": "PCT", "value": "10"},
        headers=admin,
    )
    assert resp.status_code == 422
    # FLAT without a sane min_subtotal rejected (free-food guard)
    resp = await client.post(
        "/api/v1/admin/coupons",
        json={"code": "FREEFOOD", "type": "FLAT", "value": "100", "min_subtotal": "120"},
        headers=admin,
    )
    assert resp.status_code == 422
    # duplicate code is 409
    ok = {"code": "DUP1", "type": "FLAT", "value": "50", "min_subtotal": "100"}
    assert (await client.post("/api/v1/admin/coupons", json=ok, headers=admin)).status_code == 201
    assert (await client.post("/api/v1/admin/coupons", json=ok, headers=admin)).status_code == 409


async def test_admin_requires_role(client, customer):
    resp = await client.get("/api/v1/admin/coupons", headers=customer)
    assert resp.status_code == 403
