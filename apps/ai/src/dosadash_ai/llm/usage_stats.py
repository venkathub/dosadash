"""Provider prompt-cache observability (Phase 9 hardening).

The order agent's message layout is deliberately prefix-stable (static
system prompt + menu context first) so OpenAI's implicit prompt caching
kicks in — but until now we never MEASURED it. litellm surfaces the
provider's `usage.prompt_tokens_details.cached_tokens`; this module
accumulates those numbers in Redis so the admin Costs tab can show the
real cached-token share instead of a hand-wave.

Counters live in one Redis hash (`cachestats:prompt`) on the cache Redis.
allkeys-lru may evict it under pressure — acceptable: these are running
indicators, not billing records (billing truth stays in Langfuse).

Best-effort everywhere: recording must never fail an LLM call.
"""

import logging
from typing import Any

from redis.asyncio import Redis

from dosadash_ai.config import get_settings

logger = logging.getLogger(__name__)

PROMPT_STATS_KEY = "cachestats:prompt"


def extract_usage(response: Any) -> tuple[int, int, int]:
    """(prompt_tokens, cached_prompt_tokens, completion_tokens) from a
    litellm response — defensive: Groq/Gemini omit cached-token details."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0, 0)
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
    return (prompt, cached, completion)


class UsageStats:
    """Redis-hash accumulator (injectable client for tests)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    async def record_response(self, response: Any) -> None:
        """Accumulate one completion's token usage. Never raises."""
        prompt, cached, completion = extract_usage(response)
        if prompt == 0 and completion == 0:
            return
        try:
            redis = self._client()
            async with redis.pipeline(transaction=False) as pipe:
                pipe.hincrby(PROMPT_STATS_KEY, "calls", 1)
                pipe.hincrby(PROMPT_STATS_KEY, "prompt_tokens", prompt)
                pipe.hincrby(PROMPT_STATS_KEY, "cached_prompt_tokens", cached)
                pipe.hincrby(PROMPT_STATS_KEY, "completion_tokens", completion)
                await pipe.execute()
        except Exception:  # noqa: BLE001 — observability must never break calls
            logger.warning("usage stats: record failed, skipping")

    async def snapshot(self) -> dict[str, int]:
        """Current counters ({} on Redis failure). Never raises."""
        try:
            raw = await self._client().hgetall(PROMPT_STATS_KEY)
            return {k: int(v) for k, v in raw.items()}
        except Exception:  # noqa: BLE001
            logger.warning("usage stats: snapshot failed")
            return {}


_stats: UsageStats | None = None


def get_usage_stats() -> UsageStats:
    global _stats  # noqa: PLW0603 — module singleton, overridable in tests
    if _stats is None:
        _stats = UsageStats()
    return _stats


def set_usage_stats(stats: UsageStats | None) -> None:
    """Test hook."""
    global _stats
    _stats = stats
