"""Public /api/v1/recs tests — AI client is faked (no network)."""

from decimal import Decimal

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import CheckoutSuggestion, CheckoutSuggestResponse, RecItem, RecsResponse, Role

RECS = "/api/v1/recs"


class FakeAIClient:
    def __init__(self, fail: bool = False) -> None:
        self.requests = []
        self.fail = fail

    async def recommend(self, request):
        self.requests.append(request)
        if self.fail:
            raise AIServiceError("ai down")
        return RecsResponse(
            items=[
                RecItem(
                    item_id=3,
                    name="Filter Coffee",
                    price=Decimal("60.00"),
                    is_veg=True,
                    score=1.0,
                )
            ],
            source="popular",
            model_version=None,
        )


@pytest.fixture
def ai(client):
    from dosadash_api.main import app

    fake = FakeAIClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


async def test_anonymous_recs(client, ai):
    resp = await client.get(f"{RECS}?cart=1,2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["name"] == "Filter Coffee"
    req = ai.requests[0]
    assert req.user_id is None
    assert req.cart_item_ids == [1, 2]


async def test_authed_recs_carry_user_id(client, ai, db_session):
    user = User(phone="+919555559011", name="Recs Customer", role=Role.CUSTOMER)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    resp = await client.get(RECS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert ai.requests[0].user_id == user.id


async def test_cart_junk_is_dropped(client, ai):
    resp = await client.get(f"{RECS}?cart=1,x,;drop table,3,")
    assert resp.status_code == 200
    assert ai.requests[0].cart_item_ids == [1, 3]


async def test_ai_failure_degrades_to_empty(client):
    from dosadash_api.main import app

    fake = FakeAIClient(fail=True)
    app.dependency_overrides[get_ai_client] = lambda: fake
    try:
        resp = await client.get(RECS)
        assert resp.status_code == 200  # the menu page must never break on recs
        body = resp.json()
        assert body["items"] == []
        assert body["source"] == "unavailable"
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_k_is_bounded(client, ai):
    assert (await client.get(f"{RECS}?k=50")).status_code == 422
    assert (await client.get(f"{RECS}?k=0")).status_code == 422


# ------------------------------------------------------- checkout suggester


class FakeSuggestClient(FakeAIClient):
    async def suggest_checkout(self, request):
        self.requests.append(request)
        if self.fail:
            raise AIServiceError("ai down")
        return CheckoutSuggestResponse(
            suggestions=[
                CheckoutSuggestion(
                    item_id=3,
                    name="Filter Coffee",
                    price=Decimal("60.00"),
                    is_veg=True,
                    kind="pairing",
                    reason="Goes well with your order",
                )
            ],
            source="als",
            model_version="dosadash-recsys/v1",
        )


@pytest.fixture
def suggest_ai(client):
    from dosadash_api.main import app

    fake = FakeSuggestClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


async def test_checkout_suggestions(client, suggest_ai):
    resp = await client.get(f"{RECS}/checkout?cart=1,2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["suggestions"][0]["kind"] == "pairing"
    assert suggest_ai.requests[0].cart_item_ids == [1, 2]


async def test_checkout_empty_cart_short_circuits(client, suggest_ai):
    resp = await client.get(f"{RECS}/checkout")
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []
    assert suggest_ai.requests == []  # ai never called without a cart


async def test_checkout_ai_failure_degrades(client):
    from dosadash_api.main import app

    fake = FakeSuggestClient(fail=True)
    app.dependency_overrides[get_ai_client] = lambda: fake
    try:
        resp = await client.get(f"{RECS}/checkout?cart=1")
        assert resp.status_code == 200
        assert resp.json() == {
            "suggestions": [],
            "source": "unavailable",
            "model_version": None,
        }
    finally:
        app.dependency_overrides.pop(get_ai_client, None)
