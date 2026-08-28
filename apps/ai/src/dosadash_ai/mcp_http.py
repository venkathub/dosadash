"""Hosted remote MCP endpoint (Phase 16): the SAME server, over HTTP.

The Phase 6 stdio adapter (`dosadash_ai.mcp_server`) is mounted here as a
Streamable HTTP app at `/mcp` so remote MCP clients — ChatGPT developer-mode
connectors, Claude Code (web/CLI), Cursor, Claude Desktop custom
connectors — can connect without running anything locally. Stateless +
JSON-response mode: no session affinity, proxy-friendly, zero extra RAM
(Hard Rule 7 — it rides the existing ai container).

Auth (admin-issued keys, generated in the admin GUI → MCP tab):

- ``Authorization: Bearer ddk_…``  — Cursor, Claude Code, Claude Desktop
- ``/mcp/ddk_…`` tokenized path    — ChatGPT's connector UI has no header
  field, so the key travels as a path segment instead

Keys are verified against the core api (`POST /internal/mcp/verify-key`,
same internal-token trust boundary the tools already use) — the api OWNS
the key table; ai only asks, with a short in-process cache so steady-state
traffic costs no extra hop. Verification fails CLOSED: if the api is
unreachable, /mcp is down (it can place real orders — Hard Rule 2 energy).

The SDK's DNS-rebinding protection is disabled deliberately: this endpoint
is meant to be public behind Caddy/the front proxy (host routing happens
there), and auth is ours.
"""

import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from dosadash_ai.mcp_server import _api_url, _internal_headers, server
from dosadash_shared import MCP_KEY_PREFIX

logger = logging.getLogger(__name__)

# Verify-cache TTLs: a revoked key keeps working for at most OK_TTL seconds
# (documented in the admin GUI); failures re-check quickly.
VERIFY_OK_TTL = 60.0
VERIFY_FAIL_TTL = 10.0
_CACHE_MAX = 512

_cache: dict[str, tuple[bool, float]] = {}


async def _remote_verify(key: str) -> bool:
    """Ask the api whether this key is live. Network/5xx → False (fail
    closed). Split out so tests can fake the network without HTTP."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_api_url()}/api/v1/internal/mcp/verify-key",
                json={"key": key},
                headers=_internal_headers(),
            )
        return resp.status_code == 200 and resp.json().get("ok") is True
    except (httpx.HTTPError, ValueError):
        logger.warning("mcp key verify failed (api unreachable?)", exc_info=True)
        return False


async def verify_key(key: str) -> bool:
    """Cached verification. Only the key's sha256 is held in memory —
    plaintext keys never linger (Rule 9 spirit)."""
    digest = hashlib.sha256(key.encode()).hexdigest()
    now = time.monotonic()
    hit = _cache.get(digest)
    if hit is not None and hit[1] > now:
        return hit[0]
    ok = await _remote_verify(key)
    if len(_cache) >= _CACHE_MAX:  # bounded: drop expired, else start over
        live = {k: v for k, v in _cache.items() if v[1] > now}
        _cache.clear()
        _cache.update(live if len(live) < _CACHE_MAX else {})
    _cache[digest] = (ok, now + (VERIFY_OK_TTL if ok else VERIFY_FAIL_TTL))
    return ok


def _build_inner():  # noqa: ANN202 — Starlette app
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


async def _unauthorized(send: Send) -> None:
    body = b'{"detail":"Missing or invalid MCP API key"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _unavailable(send: Send) -> None:
    body = b'{"detail":"MCP transport not started"}'
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class McpAuthApp:
    """Auth + dispatch for the hosted MCP endpoint.

    The SDK's StreamableHTTPSessionManager is single-run per instance, so
    the transport app is built fresh inside each lifespan (prod: once per
    process; tests: once per fixture) rather than at import time."""

    def __init__(self) -> None:
        self._inner = None

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Runs the transport's session manager — FastAPI does not
        propagate lifespans into ASGI middleware, so the ai app's lifespan
        calls this."""
        inner = _build_inner()
        self._inner = inner
        try:
            async with inner.router.lifespan_context(inner):
                yield
        finally:
            self._inner = None

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """`scope['path']` arrives WITHOUT the /mcp prefix ('' | '/' |
        '/<key>' …). Key comes from Bearer header or first path segment."""
        if self._inner is None:  # lifespan not running (startup race)
            await _unavailable(send)
            return
        path = scope.get("path") or "/"
        segment = path.strip("/").split("/", 1)[0]
        if segment.startswith(MCP_KEY_PREFIX):
            key = segment  # tokenized URL (ChatGPT)
        else:
            key = ""
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    text = value.decode("latin-1")
                    if text.lower().startswith("bearer "):
                        key = text[7:].strip()
                    break
        if not key or not await verify_key(key):
            await _unauthorized(send)
            return
        forwarded = dict(scope)
        forwarded["path"] = "/"  # both /mcp and /mcp/<key> hit the transport root
        await self._inner(forwarded, receive, send)


mcp_auth_app = McpAuthApp()
mcp_http_lifespan = mcp_auth_app.lifespan


class McpDispatchMiddleware:
    """Pure-ASGI interception of /mcp* on the ai app (rate-limiter
    precedent: no BaseHTTPMiddleware, no router redirect_slashes — MCP
    clients must never see a 307 on POST)."""

    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/mcp" or path.startswith("/mcp/"):
                forwarded = dict(scope)
                forwarded["path"] = path[len("/mcp") :]
                await mcp_auth_app.handle(forwarded, receive, send)
                return
        await self.app(scope, receive, send)
