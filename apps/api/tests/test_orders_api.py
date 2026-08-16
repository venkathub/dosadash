"""Orders API integration tests (real DB via conftest fixtures)."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import StaffAction, User
from dosadash_api.providers import MockPaymentProvider
from dosadash_shared import Role


async def _customer(client) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9111111111"})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": "9111111111", "otp": otp})
    body = verify.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def _staff(db_session) -> dict:
    user = User(phone="+919222222222", name="Kitchen", role=Role.KITCHEN_STAFF)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        secret=get_settings().jwt_secret,
        ttl_minutes=5,
    )
    return {"Authorization": f"Bearer {token}"}


async def _menu_ids(client) -> dict[str, dict]:
    resp = await client.get("/api/v1/menu")
    return {i["name"]: i for i in resp.json()}


@pytest.fixture
async def customer(client):
    return await _customer(client)


async def test_checkout_computes_gst_and_creates_payment(client, customer):
    menu = await _menu_ids(client)
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={
            "items": [
                {"item_id": menu["Masala Dosa"]["id"], "qty": 2},
                {"item_id": menu["Filter Coffee"]["id"], "qty": 1},
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # 2*120 + 60 = 300 subtotal; 5% GST = 15.00
    assert body["subtotal"] == "300.00"
    assert body["gst"] == "15.00"
    assert body["total"] == "315.00"
    assert body["status"] == "PLACED"
    assert body["payment"]["provider"] == "mock"
    assert body["payment"]["status"] == "CREATED"
    names = {i["name"] for i in body["items"]}
    assert names == {"Masala Dosa", "Filter Coffee"}


async def test_checkout_unknown_item_404(client, customer):
    resp = await client.post(
        "/api/v1/orders", headers=customer, json={"items": [{"item_id": 999999, "qty": 1}]}
    )
    assert resp.status_code == 404


async def test_checkout_sold_out_item_409(client, customer, db_session):
    from sqlalchemy import select

    from dosadash_api.db.models import MenuItem

    off = await db_session.scalar(select(MenuItem).where(MenuItem.name == "Seasonal Special"))
    resp = await client.post(
        "/api/v1/orders", headers=customer, json={"items": [{"item_id": off.id, "qty": 1}]}
    )
    assert resp.status_code == 409
    assert "Seasonal Special" in resp.json()["detail"]


async def test_checkout_requires_auth(client):
    resp = await client.post("/api/v1/orders", json={"items": [{"item_id": 1, "qty": 1}]})
    assert resp.status_code == 401


async def test_mock_payment_verify(client, customer):
    menu = await _menu_ids(client)
    order = (
        await client.post(
            "/api/v1/orders",
            headers=customer,
            json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
        )
    ).json()
    provider_order_id = order["payment"]["provider_order_id"]
    sig = MockPaymentProvider().sign(order_id=provider_order_id, payment_id="pay_test_1")

    bad = await client.post(
        f"/api/v1/orders/{order['id']}/pay",
        headers=customer,
        json={"payment_id": "pay_test_1", "signature": "wrong"},
    )
    assert bad.status_code == 400

    good = await client.post(
        f"/api/v1/orders/{order['id']}/pay",
        headers=customer,
        json={"payment_id": "pay_test_1", "signature": sig},
    )
    assert good.status_code == 200
    assert good.json()["payment"]["status"] == "CAPTURED"
    assert good.json()["payment"]["signature_verified"] is True


async def test_customer_can_cancel_placed_only(client, customer, db_session):
    menu = await _menu_ids(client)
    order = (
        await client.post(
            "/api/v1/orders",
            headers=customer,
            json={"items": [{"item_id": menu["Filter Coffee"]["id"], "qty": 1}]},
        )
    ).json()

    cancelled = await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=customer)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    again = await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=customer)
    assert again.status_code == 403  # no longer PLACED


async def test_customer_cannot_set_kitchen_status(client, customer):
    menu = await _menu_ids(client)
    order = (
        await client.post(
            "/api/v1/orders",
            headers=customer,
            json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
        )
    ).json()
    resp = await client.post(
        f"/api/v1/orders/{order['id']}/status", headers=customer, json={"status": "CONFIRMED"}
    )
    assert resp.status_code == 403


async def test_staff_kitchen_flow_with_audit(client, customer, db_session):
    menu = await _menu_ids(client)
    order = (
        await client.post(
            "/api/v1/orders",
            headers=customer,
            json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
        )
    ).json()
    staff = await _staff(db_session)

    for status in ("CONFIRMED", "COOKING", "READY", "OUT_FOR_DELIVERY", "DELIVERED"):
        resp = await client.post(
            f"/api/v1/orders/{order['id']}/status", headers=staff, json={"status": status}
        )
        assert resp.status_code == 200, f"{status}: {resp.text}"
        assert resp.json()["status"] == status

    skip = await client.post(
        f"/api/v1/orders/{order['id']}/status", headers=staff, json={"status": "COOKING"}
    )
    assert skip.status_code == 409  # DELIVERED -> COOKING illegal

    from sqlalchemy import func, select

    audit_count = await db_session.scalar(
        select(func.count()).select_from(StaffAction).where(StaffAction.action == "order.status")
    )
    assert audit_count == 5


async def test_order_history_and_detail_isolation(client, customer, db_session):
    menu = await _menu_ids(client)
    await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
    )
    history = await client.get("/api/v1/orders", headers=customer)
    assert history.status_code == 200
    assert len(history.json()) >= 1
    order_id = history.json()[0]["id"]

    staff = await _staff(db_session)  # different user
    other_view = await client.get(f"/api/v1/orders/{order_id}", headers=staff)
    assert other_view.status_code == 200  # staff may view

    # another customer may not
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9333333333"})
    otp = req.json()["demo_otp"]
    other = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9333333333", "otp": otp})
    ).json()
    stranger = {"Authorization": f"Bearer {other['access_token']}"}
    denied = await client.get(f"/api/v1/orders/{order_id}", headers=stranger)
    assert denied.status_code == 403
