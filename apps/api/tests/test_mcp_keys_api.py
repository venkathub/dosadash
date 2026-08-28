"""Phase 16 MCP keys: admin CRUD (GUI key generation) + internal verify.

The LLM-provider key UX invariants under test: plaintext returned exactly
once, only the hash persisted, list never leaks keys, revoke wins over the
verify endpoint, verify never leaks key existence, mutations audited.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import McpApiKey, StaffAction, User
from dosadash_shared import MCP_KEY_PREFIX, Role, hash_mcp_key

KEYS = "/api/v1/admin/mcp-keys"
VERIFY = "/api/v1/internal/mcp/verify-key"


def _internal(monkeypatch) -> dict:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    get_settings.cache_clear()
    return {"X-Internal-Token": "test-internal"}


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _admin(client, db_session: AsyncSession, phone: str = "9111177801") -> dict:
    headers = await _login(client, phone)
    user = (await db_session.execute(select(User).where(User.phone.contains(phone)))).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()
    return headers


async def test_requires_admin(client, db_session) -> None:
    headers = await _login(client, "9111177802")
    assert (await client.get(KEYS, headers=headers)).status_code == 403
    assert (await client.post(KEYS, json={"name": "x"}, headers=headers)).status_code == 403


async def test_create_returns_key_once_and_stores_only_hash(
    client, db_session: AsyncSession
) -> None:
    headers = await _admin(client, db_session)
    resp = await client.post(KEYS, json={"name": "Cursor laptop"}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    key = body["key"]
    assert key.startswith(MCP_KEY_PREFIX)
    assert body["key_prefix"] == key[:12]
    assert body["name"] == "Cursor laptop"

    row = (await db_session.execute(select(McpApiKey))).scalar_one()
    assert row.key_hash == hash_mcp_key(key)
    assert key not in (row.key_prefix + row.key_hash + row.name)  # never plaintext

    # list view never contains the key again
    listing = (await client.get(KEYS, headers=headers)).json()
    assert len(listing) == 1
    assert "key" not in listing[0]
    assert listing[0]["key_prefix"] == key[:12]
    assert listing[0]["revoked_at"] is None

    # audited (Phase 2 audit trail)
    actions = (await db_session.execute(select(StaffAction.action))).scalars().all()
    assert "mcp_key.create" in actions


async def test_verify_key_roundtrip_and_revoke_wins(
    client, db_session: AsyncSession, monkeypatch
) -> None:
    internal = _internal(monkeypatch)
    headers = await _admin(client, db_session)
    key = (await client.post(KEYS, json={"name": "ChatGPT"}, headers=headers)).json()["key"]

    ok = await client.post(VERIFY, json={"key": key}, headers=internal)
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "name": "ChatGPT"}

    # last_used_at stamped on first verify
    row = (await db_session.execute(select(McpApiKey))).scalar_one()
    await db_session.refresh(row)
    assert row.last_used_at is not None

    # unknown key: same shape, no existence leak
    bad = await client.post(VERIFY, json={"key": "ddk_nope"}, headers=internal)
    assert bad.json() == {"ok": False, "name": None}

    # revoke → verify flips to ok=False, re-revoke 409
    key_id = row.id
    revoked = await client.post(f"{KEYS}/{key_id}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert (await client.post(VERIFY, json={"key": key}, headers=internal)).json()["ok"] is False
    assert (await client.post(f"{KEYS}/{key_id}/revoke", headers=headers)).status_code == 409


async def test_verify_key_requires_internal_token(client, db_session, monkeypatch):
    _internal(monkeypatch)
    assert (await client.post(VERIFY, json={"key": "ddk_x"})).status_code == 403
    assert (
        await client.post(VERIFY, json={"key": "ddk_x"}, headers={"X-Internal-Token": "nope"})
    ).status_code == 403


async def test_revoke_unknown_key_404(client, db_session) -> None:
    headers = await _admin(client, db_session)
    assert (await client.post(f"{KEYS}/999999/revoke", headers=headers)).status_code == 404
