"""Rate limiting (Phase 9): tier classification, identity buckets, 429s,
internal-token exemption, and the fail-open contract."""

import httpx
import pytest
from fastapi import FastAPI

from dosadash_api import ratelimit
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.ratelimit import (
    RateLimiter,
    RateLimitMiddleware,
    Rule,
    build_rules,
    classify,
    identity_from_scope,
)
from dosadash_shared import Role

RULES = build_rules(get_settings())


class MemoryStore:
    """Deterministic in-memory counter (no Redis in unit tests)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str, ttl_seconds: int) -> int | None:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class DeadStore:
    """Simulates a Redis outage — incr always reports failure."""

    async def incr(self, key: str, ttl_seconds: int) -> int | None:
        return None


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/chat", "chat"),
        ("POST", "/api/v1/chat/stream", "chat"),
        ("POST", "/api/v1/auth/otp/request", "auth"),
        ("POST", "/api/v1/feedback", "feedback"),
        ("GET", "/api/v1/menu", "read"),
        ("GET", "/api/v1/admin/orders", "read"),
        ("POST", "/api/v1/orders", "write"),
        ("PATCH", "/api/v1/admin/menu/1", "write"),
        ("POST", "/api/v1/aggregator/webhook", "write"),
    ],
)
def test_classify_tiers(method: str, path: str, expected: str) -> None:
    rule = classify(method, path, RULES)
    assert rule is not None and rule.name == expected


@pytest.mark.parametrize(
    "path",
    [
        "/healthz",
        "/media/menu/1.png",
        "/api/v1/internal/mcp/menu",
        "/api/v1/internal/po/decision",
        "/docs",
        "/openapi.json",
        "/somewhere-else",
    ],
)
def test_classify_exempt(path: str) -> None:
    assert classify("GET", path, RULES) is None
    assert classify("POST", path, RULES) is None


def test_chat_tier_is_strictest_llm_guard() -> None:
    """LLM-spend endpoints must be the tightest non-auth budget."""
    assert RULES["chat"].limit < RULES["write"].limit < RULES["read"].limit


# ---------------------------------------------------------------- identity


def _scope(headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None = None) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/menu",
        "headers": headers,
        "client": client,
    }


def test_identity_prefers_jwt_user() -> None:
    secret = get_settings().jwt_secret
    token = create_access_token(user_id=42, role=Role.CUSTOMER, secret=secret, ttl_minutes=5)
    scope = _scope([(b"authorization", f"Bearer {token}".encode())], ("1.2.3.4", 1))
    assert identity_from_scope(scope, secret) == "u:42"


def test_identity_invalid_token_falls_back_to_ip() -> None:
    scope = _scope([(b"authorization", b"Bearer not-a-jwt")], ("1.2.3.4", 1))
    assert identity_from_scope(scope, get_settings().jwt_secret) == "ip:1.2.3.4"


def test_identity_uses_first_forwarded_hop() -> None:
    scope = _scope([(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")], ("172.18.0.2", 1))
    assert identity_from_scope(scope, "s") == "ip:9.9.9.9"


def test_identity_falls_back_to_client_addr() -> None:
    assert identity_from_scope(_scope([], ("5.6.7.8", 1)), "s") == "ip:5.6.7.8"


# ---------------------------------------------------------------- limiter core


def _limiter(store, limit: int = 3) -> RateLimiter:
    rules = {name: Rule(name, limit) for name in ("chat", "auth", "write", "read")}
    return RateLimiter(store, rules, get_settings().jwt_secret)


async def test_limiter_blocks_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit.time, "time", lambda: 1_000_000.0)  # pin the window
    limiter = _limiter(MemoryStore(), limit=3)
    scope = _scope([], ("1.1.1.1", 1))
    for i in range(3):
        decision = await limiter.check(scope)
        assert decision.allowed and decision.remaining == 3 - (i + 1)
    blocked = await limiter.check(scope)
    assert not blocked.allowed
    assert blocked.retry_after is not None and 0 < blocked.retry_after <= 60


async def test_limiter_buckets_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit.time, "time", lambda: 1_000_000.0)
    limiter = _limiter(MemoryStore(), limit=1)
    assert (await limiter.check(_scope([], ("1.1.1.1", 1)))).allowed
    assert not (await limiter.check(_scope([], ("1.1.1.1", 1)))).allowed
    assert (await limiter.check(_scope([], ("2.2.2.2", 1)))).allowed  # other caller unaffected


async def test_limiter_fails_open_on_store_outage() -> None:
    limiter = _limiter(DeadStore(), limit=1)
    scope = _scope([], ("1.1.1.1", 1))
    for _ in range(5):
        assert (await limiter.check(scope)).allowed


async def test_limiter_disabled_short_circuits() -> None:
    limiter = _limiter(MemoryStore(), limit=1)
    limiter.enabled = False
    scope = _scope([], ("1.1.1.1", 1))
    for _ in range(5):
        assert (await limiter.check(scope)).allowed


async def test_internal_token_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "internal_api_token", "svc-token")
    limiter = _limiter(MemoryStore(), limit=1)
    scope = _scope([(b"x-internal-token", b"svc-token")], ("172.18.0.9", 1))
    for _ in range(5):
        assert (await limiter.check(scope)).allowed
    # wrong token is NOT exempt
    bad = _scope([(b"x-internal-token", b"wrong")], ("172.18.0.9", 1))
    assert (await limiter.check(bad)).allowed  # first hit consumes the budget
    assert not (await limiter.check(bad)).allowed


# ---------------------------------------------------------------- middleware


@pytest.fixture
async def limited_client(monkeypatch: pytest.MonkeyPatch):
    """Tiny app behind the real middleware, backed by a MemoryStore limiter."""
    monkeypatch.setattr(ratelimit.time, "time", lambda: 1_000_000.0)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/healthz")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    ratelimit.set_limiter(_limiter(MemoryStore(), limit=2))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    ratelimit.set_limiter(None)  # rebuild lazily for everyone else


async def test_middleware_429_with_retry_after(limited_client: httpx.AsyncClient) -> None:
    first = await limited_client.get("/api/v1/ping")
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2"
    assert first.headers["x-ratelimit-remaining"] == "1"
    await limited_client.get("/api/v1/ping")
    blocked = await limited_client.get("/api/v1/ping")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert "retry_after" in blocked.json()


async def test_middleware_healthz_never_limited(limited_client: httpx.AsyncClient) -> None:
    for _ in range(10):
        resp = await limited_client.get("/healthz")
        assert resp.status_code == 200
        assert "x-ratelimit-limit" not in resp.headers
