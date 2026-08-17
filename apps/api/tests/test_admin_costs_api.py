"""Admin cost dashboard proxy: RBAC + ai-client mapping."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import CostSummaryResponse, DailyCost, Role

COSTS = "/api/v1/admin/costs/daily"


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


class FakeAIClient:
    def __init__(self, response=None, error=False):
        self.response = response or CostSummaryResponse(
            configured=True,
            days=[DailyCost(date="2026-08-17", traces=10, observations=20, cost_usd=0.12)],
            total_cost_usd=0.12,
        )
        self.error = error
        self.days_arg = None

    async def daily_costs(self, days=30):
        if self.error:
            raise AIServiceError("ai down")
        self.days_arg = days
        return self.response


@pytest.fixture
def fake_ai(client_app):
    fake = FakeAIClient()
    client_app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    client_app.dependency_overrides.pop(get_ai_client, None)


@pytest.fixture
def client_app():
    from dosadash_api.main import app

    return app


async def test_costs_require_admin(client, db_session):
    assert (await client.get(COSTS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555557002", Role.KITCHEN_STAFF)
    assert (await client.get(COSTS, headers=kitchen)).status_code == 403


async def test_costs_proxy_happy_path(client, admin, fake_ai):
    resp = await client.get(f"{COSTS}?days=7", headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["total_cost_usd"] == pytest.approx(0.12)
    assert body["days"][0]["date"] == "2026-08-17"
    assert fake_ai.days_arg == 7


async def test_costs_ai_failure_maps_to_502(client, admin, client_app):
    client_app.dependency_overrides[get_ai_client] = lambda: FakeAIClient(error=True)
    try:
        assert (await client.get(COSTS, headers=admin)).status_code == 502
    finally:
        client_app.dependency_overrides.pop(get_ai_client, None)


async def test_costs_not_configured_passthrough(client, admin, client_app):
    client_app.dependency_overrides[get_ai_client] = lambda: FakeAIClient(
        response=CostSummaryResponse(configured=False)
    )
    try:
        body = (await client.get(COSTS, headers=admin)).json()
        assert body["configured"] is False
        assert body["days"] == []
    finally:
        client_app.dependency_overrides.pop(get_ai_client, None)
