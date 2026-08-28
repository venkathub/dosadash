"""Hosted /mcp endpoint (Phase 16): auth wrapper + Streamable HTTP handshake.

Network-free: `_remote_verify` is monkeypatched (the api-side truth lives in
apps/api/tests/test_mcp_keys_api.py). The handshake tests speak real MCP
JSON-RPC through the dispatch middleware in stateless JSON mode — exactly
what ChatGPT/Cursor/Claude Code send.
"""

from contextlib import asynccontextmanager

import httpx
import pytest

from dosadash_ai import mcp_http

_ORIG_REMOTE_VERIFY = mcp_http._remote_verify  # captured before fixtures patch it

GOOD_KEY = "ddk_test-good-key"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


@pytest.fixture(autouse=True)
def _fake_verify(monkeypatch):
    calls = {"n": 0}

    async def fake(key: str) -> bool:
        calls["n"] += 1
        return key == GOOD_KEY

    monkeypatch.setattr(mcp_http, "_remote_verify", fake)
    mcp_http._cache.clear()
    yield calls
    mcp_http._cache.clear()


@asynccontextmanager
async def _mcp_client():
    """Lifespan + client opened INSIDE the test's task — anyio task groups
    (the SDK session manager) refuse cross-task enter/exit, and
    pytest-asyncio fixtures tear down in a different task."""
    from dosadash_ai.main import app

    async with mcp_http.mcp_http_lifespan():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_no_key_401() -> None:
    async with _mcp_client() as client:
        resp = await client.post("/mcp", json=INIT, headers=HEADERS)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_bad_key_401_both_shapes() -> None:
    async with _mcp_client() as client:
        bearer = {**HEADERS, "Authorization": "Bearer ddk_wrong"}
        assert (await client.post("/mcp", json=INIT, headers=bearer)).status_code == 401
        assert (
            await client.post("/mcp/ddk_wrong", json=INIT, headers=HEADERS)
        ).status_code == 401


async def test_bearer_header_handshake() -> None:
    async with _mcp_client() as client:
        bearer = {**HEADERS, "Authorization": f"Bearer {GOOD_KEY}"}
        resp = await client.post("/mcp", json=INIT, headers=bearer)
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["serverInfo"]["name"] == "dosadash"


async def test_tokenized_path_lists_the_three_tools() -> None:
    async with _mcp_client() as client:
        # ChatGPT-style: key in the URL, no Authorization header
        resp = await client.post(f"/mcp/{GOOD_KEY}", json=TOOLS_LIST, headers=HEADERS)
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {"get_menu", "check_inventory", "place_order"}


async def test_verify_cache_avoids_repeat_lookups(_fake_verify) -> None:
    async with _mcp_client() as client:
        bearer = {**HEADERS, "Authorization": f"Bearer {GOOD_KEY}"}
        await client.post("/mcp", json=TOOLS_LIST, headers=bearer)
        await client.post("/mcp", json=TOOLS_LIST, headers=bearer)
    assert _fake_verify["n"] == 1  # second request served from cache


async def test_transport_not_started_503() -> None:
    # Outside any lifespan the dispatcher answers 503, never a crash.
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bearer = {**HEADERS, "Authorization": f"Bearer {GOOD_KEY}"}
        resp = await client.post("/mcp", json=INIT, headers=bearer)
    assert resp.status_code == 503


async def test_other_ai_routes_untouched() -> None:
    # The dispatch middleware must be a no-op for every non-/mcp path.
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "ai"


async def test_remote_verify_fails_closed_when_api_unreachable(monkeypatch) -> None:
    # /mcp can place real orders — an unreachable api must mean 'deny',
    # never 'allow' (the wrapper then answers 401).
    monkeypatch.setenv("DOSADASH_API_URL", "http://127.0.0.1:9")  # dead port
    monkeypatch.setenv("DOSADASH_INTERNAL_TOKEN", "irrelevant")
    assert await _ORIG_REMOTE_VERIFY("ddk_any") is False
