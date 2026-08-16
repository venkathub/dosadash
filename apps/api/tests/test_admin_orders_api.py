"""Admin order management tests: list/filter, modify pre-COOKING, cancel
with reason, refund flow (provider call + state machine + audit)."""

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Payment, StaffAction, User
from dosadash_api.providers import MockPaymentProvider
from dosadash_shared import Role

ADMIN_ORDERS = "/api/v1/admin/orders"


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
    return await _login_as(db_session, "+919555556001", Role.ADMIN)


@pytest.fixture
async def customer(client):
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9888888901"})
    otp = req.json()["demo_otp"]
    body = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9888888901", "otp": otp})
    ).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def _menu(client) -> dict[str, dict]:
    return {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}


async def _place_order(client, customer, items: list[dict]) -> dict:
    resp = await client.post("/api/v1/orders", headers=customer, json={"items": items})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _pay(client, customer, order: dict) -> dict:
    sig = MockPaymentProvider().sign(
        order_id=order["payment"]["provider_order_id"], payment_id="pay_test_ref"
    )
    resp = await client.post(
        f"/api/v1/orders/{order['id']}/pay",
        headers=customer,
        json={"payment_id": "pay_test_ref", "signature": sig},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------------ RBAC


async def test_admin_orders_rbac(client, db_session):
    assert (await client.get(ADMIN_ORDERS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555556002", Role.KITCHEN_STAFF)
    assert (await client.get(ADMIN_ORDERS, headers=kitchen)).status_code == 403


# ------------------------------------------------------------------------ list


async def test_list_orders_with_filters(client, admin, customer):
    menu = await _menu(client)
    o1 = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    o2 = await _place_order(client, customer, [{"item_id": menu["Filter Coffee"]["id"], "qty": 2}])
    await client.post(f"/api/v1/orders/{o2['id']}/cancel", headers=customer)

    all_orders = await client.get(ADMIN_ORDERS, headers=admin)
    assert all_orders.status_code == 200
    ids = [o["id"] for o in all_orders.json()]
    assert o2["id"] in ids and o1["id"] in ids
    assert ids.index(o2["id"]) < ids.index(o1["id"])  # newest first

    placed = await client.get(ADMIN_ORDERS, headers=admin, params={"status": "PLACED"})
    assert {o["status"] for o in placed.json()} == {"PLACED"}

    limited = await client.get(ADMIN_ORDERS, headers=admin, params={"limit": 1})
    assert len(limited.json()) == 1


# ---------------------------------------------------------------- modify items


async def test_modify_items_recomputes_totals_with_audit(client, admin, customer, db_session):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    assert order["total"] == "126.00"  # 120 + 5% GST

    resp = await client.patch(
        f"{ADMIN_ORDERS}/{order['id']}/items",
        headers=admin,
        json={
            "items": [
                {"item_id": menu["Masala Dosa"]["id"], "qty": 2},
                {"item_id": menu["Filter Coffee"]["id"], "qty": 1},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subtotal"] == "300.00"  # 2*120 + 60
    assert body["gst"] == "15.00"
    assert body["total"] == "315.00"
    assert {i["name"]: i["qty"] for i in body["items"]} == {"Masala Dosa": 2, "Filter Coffee": 1}

    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "order.modify")
    )
    assert action.detail["old_total"] == "126.00"
    assert action.detail["new_total"] == "315.00"


async def test_modify_items_validates_and_blocks_after_cooking(client, admin, customer, db_session):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])

    # unknown item → 404 (Hard Rule 2: DB-validated)
    resp = await client.patch(
        f"{ADMIN_ORDERS}/{order['id']}/items",
        headers=admin,
        json={"items": [{"item_id": 999999, "qty": 1}]},
    )
    assert resp.status_code == 404

    # sold-out item → 409
    from dosadash_api.db.models import MenuItem

    off = await db_session.scalar(select(MenuItem).where(MenuItem.name == "Seasonal Special"))
    resp = await client.patch(
        f"{ADMIN_ORDERS}/{order['id']}/items",
        headers=admin,
        json={"items": [{"item_id": off.id, "qty": 1}]},
    )
    assert resp.status_code == 409

    # once COOKING, modification is refused
    for status in ("CONFIRMED", "COOKING"):
        await client.post(
            f"/api/v1/orders/{order['id']}/status", headers=admin, json={"status": status}
        )
    resp = await client.patch(
        f"{ADMIN_ORDERS}/{order['id']}/items",
        headers=admin,
        json={"items": [{"item_id": menu["Filter Coffee"]["id"], "qty": 1}]},
    )
    assert resp.status_code == 409
    assert "COOKING" in resp.json()["detail"]


# ---------------------------------------------------------------------- cancel


async def test_admin_cancel_records_reason(client, admin, customer, db_session):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    resp = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/cancel",
        headers=admin,
        json={"reason": "customer called to cancel"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

    action = await db_session.scalar(
        select(StaffAction)
        .where(StaffAction.action == "order.status")
        .order_by(StaffAction.id.desc())
    )
    assert action.detail["to"] == "CANCELLED"
    assert action.detail["note"] == "customer called to cancel"

    again = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/cancel", headers=admin, json={"reason": "twice"}
    )
    assert again.status_code == 409  # CANCELLED → CANCELLED illegal


# ---------------------------------------------------------------------- refund


async def test_refund_flow_full(client, admin, customer, db_session):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    await _pay(client, customer, order)

    # not yet refundable (PLACED → REFUNDED illegal)
    early = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/refund", headers=admin, json={"reason": "too early"}
    )
    assert early.status_code == 409

    for status in ("CONFIRMED", "COOKING", "READY", "OUT_FOR_DELIVERY", "DELIVERED"):
        await client.post(
            f"/api/v1/orders/{order['id']}/status", headers=admin, json={"status": status}
        )

    resp = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/refund",
        headers=admin,
        json={"reason": "dosa arrived cold"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "REFUNDED"
    assert body["payment"]["status"] == "REFUNDED"
    assert body["payment"]["refund_id"].startswith("rfnd_mock_")

    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "order.refund")
    )
    assert action.detail["amount"] == "126.00"
    assert action.detail["reason"] == "dosa arrived cold"


async def test_refund_requires_captured_payment(client, admin, customer):
    menu = await _menu(client)
    order = await _place_order(
        client, customer, [{"item_id": menu["Filter Coffee"]["id"], "qty": 1}]
    )
    await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=customer)

    # CANCELLED is refundable state-wise, but payment was never captured
    resp = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/refund", headers=admin, json={"reason": "never paid"}
    )
    assert resp.status_code == 409
    assert "captured" in resp.json()["detail"]


async def test_refund_amount_validation(client, admin, customer):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    await _pay(client, customer, order)
    for status in ("CONFIRMED", "CANCELLED"):
        await client.post(
            f"/api/v1/orders/{order['id']}/status", headers=admin, json={"status": status}
        )
    resp = await client.post(
        f"{ADMIN_ORDERS}/{order['id']}/refund",
        headers=admin,
        json={"amount": "9999.00", "reason": "over-refund attempt"},
    )
    assert resp.status_code == 409


async def test_pay_stores_provider_payment_id(client, customer, db_session):
    menu = await _menu(client)
    order = await _place_order(client, customer, [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}])
    await _pay(client, customer, order)
    payment = await db_session.scalar(select(Payment).where(Payment.order_id == order["id"]))
    assert payment.provider_payment_id == "pay_test_ref"
