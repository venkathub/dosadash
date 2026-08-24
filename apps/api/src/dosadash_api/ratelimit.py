"""Inbound rate limiting (Phase 9 hardening — docs/05 week 12).

Fixed-window Redis counters keyed on caller identity: JWT user id when a
valid access token is presented, else client IP (first X-Forwarded-For hop —
trustworthy because only Caddy can reach the api in compose). Tiers are
priced by cost exposure:

    chat  — /api/v1/chat/*                        (LLM spend)      20/min
    auth  — /api/v1/auth/*                        (OTP abuse)      10/min
    feedback — /api/v1/feedback                   (GitHub + triage) 5/min
    write — other POST/PATCH/PUT/DELETE /api/v1   (DB mutations)   60/min
    read  — GET/HEAD under /api/v1                (cheap)         240/min

Exempt: /healthz, /media/*, /api/v1/internal/*, websockets, and any request
presenting the valid X-Internal-Token (bot→api telegram traffic funnels many
end users through one service IP — starving it would break every Telegram
user at once).

Fail-open by design (events.py philosophy): a Redis outage must never take
checkout down — limiting silently disables until Redis returns.

Implemented as pure ASGI middleware (NOT BaseHTTPMiddleware) so the SSE
chat stream passes through untouched.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import jwt as pyjwt
from redis.asyncio import Redis

from dosadash_api.auth.security import decode_access_token
from dosadash_api.config import Settings, get_settings

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60
# Key TTL slack past the window end so clocks skewing mid-INCR never orphan keys.
_KEY_TTL_SLACK = 5

_EXEMPT_PREFIXES = ("/healthz", "/media", "/api/v1/internal/", "/docs", "/openapi.json")


@dataclass(frozen=True)
class Rule:
    """One limiting tier: `limit` requests per fixed `window_seconds` window."""

    name: str
    limit: int
    window_seconds: int = _WINDOW_SECONDS


@dataclass(frozen=True)
class Decision:
    """Outcome of a limiter check for one request."""

    allowed: bool
    rule: Rule | None = None
    remaining: int | None = None
    retry_after: int | None = None


def build_rules(settings: Settings) -> dict[str, Rule]:
    """Tier table from settings (env-tunable without a deploy-time code edit)."""
    return {
        "chat": Rule("chat", settings.rate_limit_chat_per_minute),
        "auth": Rule("auth", settings.rate_limit_auth_per_minute),
        "feedback": Rule("feedback", settings.rate_limit_feedback_per_minute),
        "write": Rule("write", settings.rate_limit_write_per_minute),
        "read": Rule("read", settings.rate_limit_read_per_minute),
    }


def classify(method: str, path: str, rules: dict[str, Rule]) -> Rule | None:
    """Map one request to its tier; None = exempt from limiting."""
    for prefix in _EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return None
    if not path.startswith("/api/v1/"):
        return None
    if path.startswith("/api/v1/chat"):
        return rules["chat"]
    if path.startswith("/api/v1/auth"):
        return rules["auth"]
    if path.startswith("/api/v1/feedback"):
        return rules["feedback"]
    if method in ("GET", "HEAD"):
        return rules["read"]
    return rules["write"]


def _header(scope: dict[str, Any], name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def identity_from_scope(scope: dict[str, Any], jwt_secret: str) -> str:
    """`u:<id>` for a valid bearer token, else `ip:<addr>`.

    An invalid/expired token falls back to IP — the ROUTE still 401s it;
    the limiter only needs a stable bucket, not an auth verdict.
    """
    auth = _header(scope, b"authorization")
    if auth and auth.lower().startswith("bearer "):
        try:
            payload = decode_access_token(auth[7:], jwt_secret)
            return f"u:{payload['sub']}"
        except pyjwt.PyJWTError:
            pass
    forwarded = _header(scope, b"x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = scope.get("client")
    return f"ip:{client[0]}" if client else "ip:unknown"


class WindowStore(Protocol):
    """Counter backend. Returns the post-increment count, or None on failure
    (None → fail open)."""

    async def incr(self, key: str, ttl_seconds: int) -> int | None: ...


class RedisWindowStore:
    """INCR + EXPIRE-on-first-hit against the cache Redis (allkeys-lru is
    fine for counters — an evicted counter just resets a window early)."""

    def __init__(self, redis_url: str) -> None:
        self._redis: Redis = Redis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5
        )

    async def incr(self, key: str, ttl_seconds: int) -> int | None:
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, ttl_seconds, nx=True)
                count, _ = await pipe.execute()
            return int(count)
        except Exception:  # noqa: BLE001 — fail open by design
            logger.warning("rate-limit store unavailable — failing open")
            return None


class RateLimiter:
    """Classify → identify → count. One instance per process (module singleton)."""

    def __init__(
        self, store: WindowStore, rules: dict[str, Rule], jwt_secret: str, *, enabled: bool = True
    ) -> None:
        self.store = store
        self.rules = rules
        self.jwt_secret = jwt_secret
        self.enabled = enabled

    async def check(self, scope: dict[str, Any]) -> Decision:
        if not self.enabled:
            return Decision(allowed=True)
        rule = classify(scope.get("method", "GET"), scope.get("path", ""), self.rules)
        if rule is None:
            return Decision(allowed=True)
        internal = _header(scope, b"x-internal-token")
        expected = get_settings().internal_api_token
        if internal and expected and internal == expected:
            return Decision(allowed=True)
        identity = identity_from_scope(scope, self.jwt_secret)
        now = int(time.time())
        window_index = now // rule.window_seconds
        key = f"rl:{rule.name}:{identity}:{window_index}"
        count = await self.store.incr(key, rule.window_seconds + _KEY_TTL_SLACK)
        if count is None:  # store outage → fail open
            return Decision(allowed=True)
        retry_after = rule.window_seconds - (now % rule.window_seconds)
        if count > rule.limit:
            return Decision(allowed=False, rule=rule, remaining=0, retry_after=retry_after)
        return Decision(allowed=True, rule=rule, remaining=rule.limit - count)


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """Process singleton (lazy so tests can swap the store before first use)."""
    global _limiter
    if _limiter is None:
        settings = get_settings()
        _limiter = RateLimiter(
            RedisWindowStore(settings.redis_url),
            build_rules(settings),
            settings.jwt_secret,
            enabled=settings.rate_limit_enabled,
        )
    return _limiter


def set_limiter(limiter: RateLimiter | None) -> None:
    """Test hook: install a limiter with a fake store (None → rebuild lazily)."""
    global _limiter
    _limiter = limiter


class RateLimitMiddleware:
    """Pure ASGI wrapper: 429 + Retry-After when over budget, X-RateLimit-*
    headers when counted, byte-for-byte passthrough otherwise."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        decision = await get_limiter().check(scope)
        if not decision.allowed:
            assert decision.rule is not None
            retry = decision.retry_after or decision.rule.window_seconds
            body = (
                b'{"detail":"Rate limit exceeded. Slow down and retry shortly.",'
                b'"retry_after":%d}' % retry
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(retry).encode()),
                        (b"x-ratelimit-limit", str(decision.rule.limit).encode()),
                        (b"x-ratelimit-remaining", b"0"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        if decision.rule is None or decision.remaining is None:
            await self.app(scope, receive, send)
            return

        rule, remaining = decision.rule, decision.remaining

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(rule.limit).encode()))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
