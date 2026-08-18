"""Copilot RBAC proxy."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import CopilotAnswer, Role

ASK = "/api/v1/admin/copilot/ask"


async def _login_as(db_session, phone: str, role: Role) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


class FakeAIClient:
    def __init__(self, error=False):
        self.error = error
        self.seen = None

    async def copilot_ask(self, request, *, admin_user_id):
        if self.error:
            raise AIServiceError("down")
        self.seen = (request.question, admin_user_id)
        return CopilotAnswer(
            question=request.question,
            sql="SELECT 1 LIMIT 1",
            columns=["c"],
            rows=[[1]],
            row_count=1,
        )


@pytest.fixture
def fake_ai(client):
    from dosadash_api.main import app

    fake = FakeAIClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


async def test_rbac(client, db_session, fake_ai):
    customer = await _login_as(db_session, "+919555550301", Role.CUSTOMER)
    resp = await client.post(ASK, json={"question": "top dishes?"}, headers=customer)
    assert resp.status_code == 403


async def test_proxies_with_admin_identity(client, db_session, fake_ai):
    admin = await _login_as(db_session, "+919555550302", Role.ADMIN)
    resp = await client.post(ASK, json={"question": "top dishes this week?"}, headers=admin)
    assert resp.status_code == 200
    assert resp.json()["rows"] == [[1]]
    question, admin_id = fake_ai.seen
    assert question == "top dishes this week?"
    assert isinstance(admin_id, int)


async def test_ai_down_maps_502(client, db_session, fake_ai):
    fake_ai.error = True
    admin = await _login_as(db_session, "+919555550303", Role.ADMIN)
    resp = await client.post(ASK, json={"question": "top dishes?"}, headers=admin)
    assert resp.status_code == 502
