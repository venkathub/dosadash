"""/internal/agent/chat endpoint: token guard, happy path, failure mapping."""

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.db import get_session
from dosadash_ai.llm.client import LLMError
from dosadash_ai.routers import agent as agent_router


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_chat_requires_internal_token(ai_client):
    resp = await ai_client.post("/internal/agent/chat", json={"message": "2 dosas"})
    assert resp.status_code == 403


async def test_chat_happy_path(ai_client, monkeypatch):
    from dosadash_shared import AgentChatResponse, OrderDraft

    async def fake_run_turn(session, req):
        assert req.message == "2 masala dosas"
        return AgentChatResponse(
            reply="Two Masala Dosas added — anything else?",
            draft=OrderDraft(),
            ready_to_place=False,
            kitchen_open=True,
            model="gpt-4o-mini",
        )

    monkeypatch.setattr(agent_router, "run_turn", fake_run_turn)
    resp = await ai_client.post(
        "/internal/agent/chat",
        json={"message": "2 masala dosas", "session_id": "chat-9"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prompt_version"] == "order_agent_v5"
    assert body["kitchen_open"] is True


async def test_chat_502_on_chain_failure(ai_client, monkeypatch):
    async def fake_run_turn(session, req):
        raise LLMError("all models down")

    monkeypatch.setattr(agent_router, "run_turn", fake_run_turn)
    resp = await ai_client.post(
        "/internal/agent/chat",
        json={"message": "2 dosas"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 502


async def test_chat_validates_input(ai_client):
    resp = await ai_client.post(
        "/internal/agent/chat",
        json={"message": ""},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422
