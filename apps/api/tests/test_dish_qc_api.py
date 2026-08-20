"""Dish-photo QC api endpoint tests — the AI client is faked (no network)."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import DishQCResult, Role

PHOTO = {"image_base64": "ZmFrZS1kaXNoLXBob3Rv", "mime_type": "image/jpeg"}


async def _customer(client) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9111112299"})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": "9111112299", "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _staff(db_session) -> dict:
    user = User(phone="+919222229901", name="QC Kitchen", role=Role.KITCHEN_STAFF)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _place_order(client, headers) -> int:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class FakeQCClient:
    def __init__(self, fail: bool = False) -> None:
        self.requests = []
        self.fail = fail

    async def qc_dish(self, request):
        self.requests.append(request)
        if self.fail:
            raise AIServiceError("ai down")
        return DishQCResult(verdict="PASS", matched=list(request.expected_dishes))


@pytest.fixture
def qc_ai(client):
    from dosadash_api.main import app

    fake = FakeQCClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


async def test_staff_qc_photo_happy_path(client, db_session, qc_ai):
    customer = await _customer(client)
    order_id = await _place_order(client, customer)
    staff = await _staff(db_session)
    resp = await client.post(f"/api/v1/orders/{order_id}/qc-photo", json=PHOTO, headers=staff)
    assert resp.status_code == 200, resp.text
    assert resp.json()["verdict"] == "PASS"
    req = qc_ai.requests[0]
    assert req.expected_dishes == ["Masala Dosa"]  # from the real order rows
    assert req.session_id.startswith("kds:")


async def test_customer_cannot_qc(client, db_session, qc_ai):
    customer = await _customer(client)
    order_id = await _place_order(client, customer)
    resp = await client.post(f"/api/v1/orders/{order_id}/qc-photo", json=PHOTO, headers=customer)
    assert resp.status_code == 403
    assert qc_ai.requests == []


async def test_qc_unknown_order_404(client, db_session, qc_ai):
    staff = await _staff(db_session)
    resp = await client.post("/api/v1/orders/999999/qc-photo", json=PHOTO, headers=staff)
    assert resp.status_code == 404


async def test_qc_ai_down_is_502(client, db_session):
    from dosadash_api.main import app

    customer = await _customer(client)
    order_id = await _place_order(client, customer)
    staff = await _staff(db_session)
    app.dependency_overrides[get_ai_client] = lambda: FakeQCClient(fail=True)
    try:
        resp = await client.post(f"/api/v1/orders/{order_id}/qc-photo", json=PHOTO, headers=staff)
        assert resp.status_code == 502
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_qc_validates_mime(client, db_session, qc_ai):
    staff = await _staff(db_session)
    resp = await client.post(
        "/api/v1/orders/1/qc-photo",
        json={"image_base64": PHOTO["image_base64"], "mime_type": "image/gif"},
        headers=staff,
    )
    assert resp.status_code == 422
