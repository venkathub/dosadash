"""Semantic cache for RAG Q&A (Phase 4 LLMOps, docs/02: ~40% cost cut).

Caches `answer_question` responses in Redis keyed by question embedding:
an incoming question reuses a cached answer when cosine similarity to a
cached question is >= the threshold (docs/06: `semcache:*`, cosine 0.95).

Scope is deliberately narrow: ONLY the stateless knowledge Q&A path.
The order agent is never cached — its turns depend on draft, preferences,
kitchen state, and 86ing (Hard Rule 4 would be violated by a stale turn).

Design:
- exact-match fast path: SHA-256 of the redacted, normalized question
- semantic path: bounded candidate list (`semcache:rag:keys`, LPUSH+LTRIM)
  scored in-process — the query embedding is already computed for
  retrieval, so a lookup costs zero extra provider calls
- entries carry a TTL; Redis runs allkeys-lru (infra), so memory is safe
- `flush()` on any menu event or knowledge re-ingest (event cascade,
  Hard Rule 4): business-state changes must never serve stale answers

Best-effort everywhere: a Redis outage degrades cost, never availability.
"""

import hashlib
import json
import logging
from typing import Any

from redis.asyncio import Redis

from dosadash_ai.config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "semcache:rag:"
_KEYS_LIST = "semcache:rag:keys"
# Phase 9 observability: hit/miss counters (outside _PREFIX so flush() —
# which drops cached ANSWERS — never wipes the running stats).
STATS_KEY = "cachestats:semcache"


def _normalize(question: str) -> str:
    return " ".join(question.lower().split())


def _entry_key(question: str) -> str:
    digest = hashlib.sha256(_normalize(question).encode()).hexdigest()[:32]
    return f"{_PREFIX}{digest}"


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; OpenAI embeddings are unit-norm so this is a dot
    product, but normalize defensively for other providers."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """Embedding-similarity cache over Redis (injectable for tests)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _client(self) -> Redis:
        if self._redis is None:
            self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    async def _bump(self, field: str, by: int = 1) -> None:
        """Best-effort stat counter — must never turn a hit into a miss."""
        try:
            await self._client().hincrby(STATS_KEY, field, by)
        except Exception:  # noqa: BLE001 — observability never breaks lookups
            logger.debug("semcache: stat bump failed (%s)", field)

    async def stats(self) -> dict[str, int]:
        """Current counters ({} on Redis failure). Never raises."""
        try:
            raw = await self._client().hgetall(STATS_KEY)
            return {k: int(v) for k, v in raw.items()}
        except Exception:  # noqa: BLE001
            logger.warning("semcache: stats read failed")
            return {}

    async def get(self, question: str, embedding: list[float]) -> dict[str, Any] | None:
        """Cached response payload, or None. Never raises."""
        settings = get_settings()
        if not settings.semcache_enabled:
            return None
        try:
            redis = self._client()
            exact = await redis.get(_entry_key(question))
            if exact is not None:
                logger.info("semcache: exact hit")
                await self._bump("exact_hits")
                return json.loads(exact)["response"]

            keys = await redis.lrange(_KEYS_LIST, 0, settings.semcache_max_candidates - 1)
            if not keys:
                await self._bump("misses")
                return None
            raw_entries = await redis.mget(keys)
            best: tuple[float, dict[str, Any]] | None = None
            for raw in raw_entries:
                if raw is None:  # expired entry still listed — skip
                    continue
                entry = json.loads(raw)
                score = cosine(embedding, entry["embedding"])
                if score >= settings.semcache_threshold and (best is None or score > best[0]):
                    best = (score, entry)
            if best is not None:
                logger.info("semcache: semantic hit (cosine %.3f)", best[0])
                await self._bump("semantic_hits")
                return best[1]["response"]
            await self._bump("misses")
            return None
        except Exception:  # noqa: BLE001 — cache failure must never break answers
            logger.warning("semcache: lookup failed, treating as miss", exc_info=True)
            return None

    async def put(self, question: str, embedding: list[float], response: dict[str, Any]) -> None:
        """Store a fresh answer. Never raises."""
        settings = get_settings()
        if not settings.semcache_enabled:
            return
        try:
            redis = self._client()
            key = _entry_key(question)
            payload = json.dumps(
                {"question": _normalize(question), "embedding": embedding, "response": response}
            )
            async with redis.pipeline(transaction=False) as pipe:
                pipe.set(key, payload, ex=settings.semcache_ttl_seconds)
                pipe.lpush(_KEYS_LIST, key)
                pipe.ltrim(_KEYS_LIST, 0, settings.semcache_max_candidates - 1)
                await pipe.execute()
            await self._bump("stores")
        except Exception:  # noqa: BLE001
            logger.warning("semcache: store failed, skipping", exc_info=True)

    async def flush(self) -> int:
        """Drop every cached answer (menu edit / knowledge re-ingest —
        Hard Rule 4). Returns entries removed; never raises."""
        try:
            redis = self._client()
            removed = 0
            async for key in redis.scan_iter(match=f"{_PREFIX}*", count=100):
                if key == _KEYS_LIST:
                    continue  # index list is deleted separately, not an entry
                await redis.delete(key)
                removed += 1
            await redis.delete(_KEYS_LIST)
            if removed:
                logger.info("semcache: flushed %d entries", removed)
            await self._bump("flushes")
            return removed
        except Exception:  # noqa: BLE001
            logger.warning("semcache: flush failed", exc_info=True)
            return 0


_cache: SemanticCache | None = None


def get_semcache() -> SemanticCache:
    global _cache  # noqa: PLW0603 — module singleton, overridable in tests
    if _cache is None:
        _cache = SemanticCache()
    return _cache
