"""Telegram linking + DM OTP channel tests."""

import httpx
import pytest
from fakeredis import aioredis as fakeaioredis
from sqlalchemy import select

from dosadash_api import events
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.providers import TelegramOtpChannel
from dosadash_bot.api_client import link_account
from dosadash_bot.render import link_failed_text, link_success_text


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    body = (await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(events, "get_redis", lambda: fake)
    # auth router imported get_redis directly — patch there too
    from dosadash_api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_token", "internal-test-token")
    return "internal-test-token"


# ------------------------------------------------------------- link flow


async def test_full_link_flow(client, db_session, fake_redis, internal_token):
    headers = await _login(client, "9800000001")

    code_resp = await client.post("/api/v1/auth/telegram/link-code", headers=headers)
    assert code_resp.status_code == 200
    body = code_resp.json()
    assert body["deep_link"].startswith("https://t.me/dosadash_bot?start=")

    link = await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": body["code"], "tg_user_id": 424242, "tg_name": "Priya"},
        headers={"X-Internal-Token": internal_token},
    )
    assert link.status_code == 200
    assert link.json()["linked"] is True

    user = await db_session.scalar(select(User).where(User.phone == "+919800000001"))
    assert user.tg_user_id == 424242
    assert user.name == "Priya"  # filled from Telegram when empty

    # code is single-use
    replay = await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": body["code"], "tg_user_id": 424242},
        headers={"X-Internal-Token": internal_token},
    )
    assert replay.status_code == 400


async def test_link_requires_internal_token(client, fake_redis, internal_token):
    headers = await _login(client, "9800000002")
    code = (await client.post("/api/v1/auth/telegram/link-code", headers=headers)).json()["code"]
    resp = await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": code, "tg_user_id": 1},
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 403


async def test_link_conflict_when_tg_account_taken(client, db_session, fake_redis, internal_token):
    h1 = await _login(client, "9800000003")
    code1 = (await client.post("/api/v1/auth/telegram/link-code", headers=h1)).json()["code"]
    await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": code1, "tg_user_id": 777},
        headers={"X-Internal-Token": internal_token},
    )
    h2 = await _login(client, "9800000004")
    code2 = (await client.post("/api/v1/auth/telegram/link-code", headers=h2)).json()["code"]
    conflict = await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": code2, "tg_user_id": 777},
        headers={"X-Internal-Token": internal_token},
    )
    assert conflict.status_code == 409


# ------------------------------------------------------- telegram otp channel


def _tg_client(ok: bool) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "sendMessage" in str(request.url)
        return httpx.Response(200 if ok else 403, json={"ok": ok})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_telegram_channel_delivers():
    channel = TelegramOtpChannel("token", 42, client=_tg_client(ok=True))
    result = await channel.send_otp("+919800000005", "123456")
    assert result.delivered is True
    assert result.demo_otp is None  # never exposed for real channels


async def test_telegram_channel_reports_failure():
    channel = TelegramOtpChannel("token", 42, client=_tg_client(ok=False))
    result = await channel.send_otp("+919800000005", "123456")
    assert result.delivered is False


async def test_otp_request_uses_telegram_for_linked_user(
    client, db_session, fake_redis, monkeypatch
):
    await _login(client, "9800000006")  # ensures the user row exists
    user = await db_session.scalar(select(User).where(User.phone == "+919800000006"))
    user.tg_user_id = 999
    await db_session.commit()
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "test-bot-token")

    sent: dict = {}

    async def fake_send(self, phone: str, otp: str):
        sent["otp"] = otp
        from dosadash_api.providers.otp import OtpSendResult

        return OtpSendResult(delivered=True, channel=self.channel_type)

    monkeypatch.setattr(TelegramOtpChannel, "send_otp", fake_send)

    # wait out cooldown by clearing prior otp rows
    from dosadash_api.db.models import OtpRequest

    for row in (await db_session.scalars(select(OtpRequest))).all():
        await db_session.delete(row)
    await db_session.commit()

    resp = await client.post("/api/v1/auth/otp/request", json={"phone": "9800000006"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "TELEGRAM"
    assert body["demo_otp"] is None  # OTP goes to the DM, not the banner
    assert "otp" in sent

    # and the DM'd OTP actually verifies
    verify = await client.post(
        "/api/v1/auth/otp/verify", json={"phone": "9800000006", "otp": sent["otp"]}
    )
    assert verify.status_code == 200


# ------------------------------------------------------------------ bot side


async def test_bot_link_account_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Token"] == "tok"
        return httpx.Response(200, json={"linked": True, "name": "Priya"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await link_account(
        api_base_url="http://api:8000",
        internal_token="tok",
        code="abc",
        tg_user_id=1,
        tg_name="Priya",
        client=client,
    )
    assert result.ok and result.name == "Priya"


async def test_bot_link_account_failure_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Invalid or expired link code"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await link_account(
        api_base_url="http://api:8000",
        internal_token="tok",
        code="bad",
        tg_user_id=1,
        tg_name=None,
        client=client,
    )
    assert not result.ok
    assert "expired" in (result.detail or "")


async def test_me_reports_link_status_and_unlink(client, db_session, fake_redis, internal_token):
    headers = await _login(client, "9800000007")
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["tg_linked"] is False

    code = (await client.post("/api/v1/auth/telegram/link-code", headers=headers)).json()["code"]
    await client.post(
        "/api/v1/auth/telegram/link",
        json={"code": code, "tg_user_id": 31337},
        headers={"X-Internal-Token": internal_token},
    )
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["tg_linked"] is True

    resp = await client.delete("/api/v1/auth/telegram/link", headers=headers)
    assert resp.status_code == 204
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    assert me["tg_linked"] is False

    user = await db_session.scalar(select(User).where(User.phone == "+919800000007"))
    await db_session.refresh(user)
    assert user.tg_user_id is None


def test_link_render_texts():
    assert "Priya" in link_success_text("Priya")
    assert "invalid or expired" in link_failed_text(None).lower()
    assert "custom reason" in link_failed_text("custom reason")
