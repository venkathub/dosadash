"""Server-error counter middleware (Phase 15 sentinel, docs/15 §S1).

Pure-ASGI (ratelimit.py precedent — BaseHTTPMiddleware breaks SSE), one
job: count 5xx responses per minute into the cache Redis so the sentinel
beat can detect error bursts without a metrics stack.

Best-effort by construction (events.py philosophy): a Redis outage or a
slow INCR must never add latency or failures to the request path — the
counter write is fire-and-forget with sub-second socket timeouts, and any
exception is swallowed. Keys: `sentinel:5xx:<epoch_minute>` with a 1-hour
TTL (running indicators, not billing records — allkeys-lru may evict).

An exception that escapes the app (no response started) counts too — it
surfaces to the client as a 500 and is exactly the burst signal we want.
"""

import logging
import time

from redis.asyncio import Redis

from dosadash_api.config import get_settings
from dosadash_api.services.sentinel import FIVEXX_KEY_PREFIX

logger = logging.getLogger(__name__)

_KEY_TTL_SECONDS = 3600


class ServerErrorCounterMiddleware:
    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app
        self._redis: Redis | None = None

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
        return self._redis

    async def _count(self) -> None:
        try:
            key = f"{FIVEXX_KEY_PREFIX}{int(time.time()) // 60}"
            redis = self._client()
            async with redis.pipeline(transaction=False) as pipe:
                pipe.incr(key)
                pipe.expire(key, _KEY_TTL_SECONDS)
                await pipe.execute()
        except Exception:  # noqa: BLE001 — counting must never hurt a request
            logger.debug("5xx counter write failed (non-blocking)", exc_info=True)

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        saw_5xx = False

        async def send_wrapper(message) -> None:  # noqa: ANN001
            nonlocal saw_5xx
            if message["type"] == "http.response.start" and message["status"] >= 500:
                saw_5xx = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await self._count()  # unhandled crash = a 500 the client saw
            raise
        if saw_5xx:
            await self._count()
