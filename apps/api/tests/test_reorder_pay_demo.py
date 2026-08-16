"""Tests for demo payment capture + 1-tap reorder."""

from sqlalchemy import select

from dosadash_api.db.models import MenuItem


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    body = (await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def _place(client, headers, item_name: str = "Masala Dosa", qty: int = 1) -> dict:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        headers=headers,
        json={"items": [{"item_id": menu[item_name]["id"], "qty": qty}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_demo_capture_marks_paid(client):
    headers = await _login(client, "9555555551")
    order = await _place(client, headers)
    resp = await client.post(f"/api/v1/orders/{order['id']}/pay/demo", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["payment"]["status"] == "CAPTURED"
    assert resp.json()["payment"]["signature_verified"] is True


async def test_demo_capture_owner_only(client):
    owner = await _login(client, "9555555552")
    stranger = await _login(client, "9555555553")
    order = await _place(client, owner)
    resp = await client.post(f"/api/v1/orders/{order['id']}/pay/demo", headers=stranger)
    assert resp.status_code == 403


async def test_reorder_creates_fresh_order(client):
    headers = await _login(client, "9555555554")
    order = await _place(client, headers, qty=2)
    resp = await client.post(f"/api/v1/orders/{order['id']}/reorder", headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != order["id"]
    assert body["status"] == "PLACED"
    assert body["total"] == order["total"]
    assert [i["qty"] for i in body["items"]] == [2]


async def test_reorder_blocked_when_item_sold_out(client, db_session):
    headers = await _login(client, "9555555555")
    order = await _place(client, headers, item_name="Filter Coffee")

    coffee = await db_session.scalar(select(MenuItem).where(MenuItem.name == "Filter Coffee"))
    coffee.is_available = False
    await db_session.commit()

    resp = await client.post(f"/api/v1/orders/{order['id']}/reorder", headers=headers)
    assert resp.status_code == 409
    assert "Filter Coffee" in resp.json()["detail"]
