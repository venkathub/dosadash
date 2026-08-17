"""Customer chat proxy tests — the AI gateway is faked (no network/LLM)."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.routers.chat import get_agent_gateway
from dosadash_shared import AgentChatResponse, OrderDraft, Role

CHAT = "/api/v1/chat"


class FakeGateway:
    def __init__(self) -> None:
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return AgentChatResponse(
            reply="Two Masala Dosas added!",
            draft=OrderDraft(),
            ready_to_place=False,
            kitchen_open=True,
            model="gpt-4o-mini",
        )

    async def stream(self, request):
        self.requests.append(request)
        yield b'data: {"type": "delta", "text": "Two"}\n\n'
        yield b'data: {"type": "final", "data": {"reply": "Two Masala Dosas added!"}}\n\n'


@pytest.fixture
def gateway(client):
    from dosadash_api.main import app

    fake = FakeGateway()
    app.dependency_overrides[get_agent_gateway] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_agent_gateway, None)


async def _customer_headers(db_session) -> dict:
    user = User(phone="+919555559001", name="Chat Customer", role=Role.CUSTOMER)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def test_anonymous_chat_allowed(client, gateway):
    resp = await client.post(CHAT, json={"message": "2 masala dosas"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "Two Masala Dosas added!"
    assert gateway.requests[0].user_id is None
    assert gateway.requests[0].session_id == "web:anon"


async def test_authed_chat_carries_user_id(client, gateway, db_session):
    headers = await _customer_headers(db_session)
    resp = await client.post(CHAT, json={"message": "my usual"}, headers=headers)
    assert resp.status_code == 200
    req = gateway.requests[0]
    assert req.user_id is not None
    assert req.session_id == f"web:{req.user_id}"


async def test_invalid_token_is_401_not_anonymous(client, gateway):
    resp = await client.post(
        CHAT, json={"message": "hi"}, headers={"Authorization": "Bearer garbage"}
    )
    assert resp.status_code == 401
    assert gateway.requests == []


async def test_stream_passthrough(client, gateway):
    resp = await client.post(f"{CHAT}/stream", json={"message": "2 dosas"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in resp.text.split("\n\n") if f]
    assert frames[0] == 'data: {"type": "delta", "text": "Two"}'
    assert "final" in frames[1]


async def test_validation(client, gateway):
    assert (await client.post(CHAT, json={"message": ""})).status_code == 422
    long_history = {"message": "hi", "history": [{"role": "user", "content": "x"}] * 25}
    assert (await client.post(CHAT, json=long_history)).status_code == 422
