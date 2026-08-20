"""Customer chat proxy tests — the AI gateway is faked (no network/LLM)."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.routers.chat import get_agent_gateway
from dosadash_shared import AgentChatResponse, OrderDraft, Role, SttResult

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

    async def transcribe(self, request):
        self.requests.append(request)
        return SttResult(
            transcript="two masala dosas and one filter coffee",
            language="en",
            model="groq/whisper-large-v3",
        )


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


# --------------------------------------------------------- telegram endpoints


def _internal(monkeypatch) -> dict:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    from dosadash_api.config import get_settings

    get_settings.cache_clear()
    return {"X-Internal-Token": "test-internal"}


async def _linked_user(db_session, tg_user_id: int) -> User:
    user = User(
        phone="+919555559002", name="TG Customer", role=Role.CUSTOMER, tg_user_id=tg_user_id
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def test_telegram_stream_resolves_linked_user(client, gateway, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    user = await _linked_user(db_session, 777001)
    resp = await client.post(
        f"{CHAT}/telegram/stream",
        json={"tg_user_id": 777001, "message": "2 dosas"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert gateway.requests[0].user_id == user.id
    assert gateway.requests[0].session_id == "tg:777001"


async def test_telegram_stream_unlinked_is_anonymous(client, gateway, monkeypatch):
    headers = _internal(monkeypatch)
    resp = await client.post(
        f"{CHAT}/telegram/stream",
        json={"tg_user_id": 999999, "message": "menu?"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert gateway.requests[0].user_id is None


async def test_telegram_stream_requires_internal_token(client, gateway, monkeypatch):
    _internal(monkeypatch)
    resp = await client.post(f"{CHAT}/telegram/stream", json={"tg_user_id": 1, "message": "hi"})
    assert resp.status_code == 403


async def test_telegram_place_creates_real_order(client, gateway, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    await _linked_user(db_session, 777002)
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        f"{CHAT}/telegram/place",
        json={
            "tg_user_id": 777002,
            "items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 2}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PLACED"
    assert body["channel"] == "TELEGRAM"
    assert body["items"][0]["name"] == "Masala Dosa"


async def test_telegram_place_unlinked_403(client, gateway, monkeypatch):
    headers = _internal(monkeypatch)
    resp = await client.post(
        f"{CHAT}/telegram/place",
        json={"tg_user_id": 424242, "items": [{"item_id": 1, "qty": 1}]},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Telegram account not linked"


# ------------------------------------------------------- telegram voice (STT)

VOICE = {"audio_base64": "ZmFrZS1vZ2ctYnl0ZXM=", "mime_type": "audio/ogg"}


async def test_telegram_stt_linked_user_enriches_trace(client, gateway, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    user = await _linked_user(db_session, 777003)
    resp = await client.post(
        f"{CHAT}/telegram/stt", json={"tg_user_id": 777003, **VOICE}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["transcript"] == "two masala dosas and one filter coffee"
    req = gateway.requests[0]
    assert req.user_id == user.id
    assert req.session_id == "tg:777003"


async def test_telegram_stt_works_unlinked(client, gateway, monkeypatch):
    headers = _internal(monkeypatch)
    resp = await client.post(
        f"{CHAT}/telegram/stt", json={"tg_user_id": 888888, **VOICE}, headers=headers
    )
    assert resp.status_code == 200
    assert gateway.requests[0].user_id is None


async def test_telegram_stt_requires_internal_token(client, gateway, monkeypatch):
    _internal(monkeypatch)
    resp = await client.post(f"{CHAT}/telegram/stt", json={"tg_user_id": 1, **VOICE})
    assert resp.status_code == 403
    assert gateway.requests == []


async def test_telegram_stt_rejects_bad_payloads(client, gateway, monkeypatch):
    headers = _internal(monkeypatch)
    bad_mime = {"tg_user_id": 1, "audio_base64": VOICE["audio_base64"], "mime_type": "audio/flac"}
    assert (
        await client.post(f"{CHAT}/telegram/stt", json=bad_mime, headers=headers)
    ).status_code == 422
    oversized = {"tg_user_id": 1, "audio_base64": "A" * 4_000_001, "mime_type": "audio/ogg"}
    assert (
        await client.post(f"{CHAT}/telegram/stt", json=oversized, headers=headers)
    ).status_code == 422
    assert gateway.requests == []
