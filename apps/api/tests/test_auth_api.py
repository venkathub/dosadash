from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from dosadash_api.db.models import OtpRequest

PHONE = "9000000001"


async def _signup(client, phone: str = PHONE) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert req.status_code == 200, req.text
    otp = req.json()["demo_otp"]
    assert otp is not None  # DEMO channel surfaces it for the banner
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    assert verify.status_code == 200, verify.text
    return verify.json()


async def test_otp_signup_creates_user_and_tokens(client):
    body = await _signup(client)
    assert body["token_type"] == "bearer"
    assert body["user"]["phone"] == "+919000000001"
    assert body["user"]["role"] == "customer"

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


async def test_wrong_otp_rejected_and_attempts_capped(client, db_session):
    await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    for _ in range(5):
        r = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "otp": "000000"})
        assert r.status_code == 400
    r = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "otp": "000000"})
    assert r.status_code == 429  # attempts exhausted


async def test_otp_is_single_use(client):
    req = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    otp = req.json()["demo_otp"]
    first = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "otp": otp})
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "otp": otp})
    assert second.status_code == 400  # burned after use


async def test_resend_cooldown(client):
    first = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    assert second.status_code == 429


async def test_expired_otp_rejected(client, db_session):
    req = await client.post("/api/v1/auth/otp/request", json={"phone": PHONE})
    otp = req.json()["demo_otp"]
    row = await db_session.scalar(select(OtpRequest).order_by(OtpRequest.id.desc()).limit(1))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    r = await client.post("/api/v1/auth/otp/verify", json={"phone": PHONE, "otp": otp})
    assert r.status_code == 400
    assert "expired" in r.json()["detail"].lower()


async def test_refresh_rotation(client):
    body = await _signup(client)
    old_refresh = body["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != old_refresh

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401  # rotated token is dead

    again = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert again.status_code == 200


async def test_logout_revokes_refresh(client):
    body = await _signup(client)
    out = await client.post("/api/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert out.status_code == 204
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401


async def test_invalid_phone_400(client):
    r = await client.post("/api/v1/auth/otp/request", json={"phone": "not-a-phone"})
    assert r.status_code == 400


async def test_me_requires_auth(client):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
