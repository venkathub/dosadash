"""Cache observability (Phase 9): semcache hit/miss counters, prompt-cache
usage accumulation through the LLM client, and the /internal/costs/cache
rollup endpoint."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fakeredis import aioredis as fakeaioredis
from pydantic import BaseModel

from dosadash_ai import config
from dosadash_ai.llm import client as client_mod
from dosadash_ai.llm import semcache as semcache_mod
from dosadash_ai.llm import usage_stats as usage_mod
from dosadash_ai.llm.semcache import SemanticCache
from dosadash_ai.llm.usage_stats import UsageStats, extract_usage

E_A = [1.0, 0.0, 0.0]
E_A_CLOSE = [0.999, 0.04, 0.0]
E_B = [0.0, 1.0, 0.0]
RESPONSE = {"answer": "yes", "citations": [], "not_found": False}


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(config.get_settings(), "semcache_enabled", True)
    return SemanticCache(redis=fakeaioredis.FakeRedis(decode_responses=True))


# ------------------------------------------------------------- semcache stats


async def test_semcache_counts_every_outcome(cache):
    await cache.put("is the dosa vegan?", E_A, RESPONSE)  # store
    await cache.get("what are your hours?", E_B)  # miss
    await cache.get("is the dosa vegan?", E_B)  # exact hit
    await cache.get("dosa — vegan or not?", E_A_CLOSE)  # semantic hit
    await cache.flush()  # flush

    stats = await cache.stats()
    assert stats["stores"] == 1
    assert stats["misses"] == 1
    assert stats["exact_hits"] == 1
    assert stats["semantic_hits"] == 1
    assert stats["flushes"] == 1


async def test_flush_preserves_stats(cache):
    """Counters live OUTSIDE the semcache:rag:* prefix — a menu-event flush
    drops answers, never the running stats."""
    await cache.put("q", E_A, RESPONSE)
    await cache.get("q", E_A)
    await cache.flush()
    stats = await cache.stats()
    assert stats["exact_hits"] == 1 and stats["stores"] == 1


class _BrokenRedis:
    def __getattr__(self, name):  # every call blows up
        raise ConnectionError("redis down")


async def test_semcache_stats_never_raise():
    broken = SemanticCache(redis=_BrokenRedis())
    assert await broken.stats() == {}
    await broken._bump("misses")  # must not raise


# ------------------------------------------------------------- usage extraction


def test_extract_usage_with_cached_details():
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
    )
    assert extract_usage(resp) == (1000, 800, 50)


def test_extract_usage_without_details_groq_gemini():
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=500, completion_tokens=20))
    assert extract_usage(resp) == (500, 0, 20)


def test_extract_usage_missing_usage():
    assert extract_usage(SimpleNamespace()) == (0, 0, 0)
    assert extract_usage(SimpleNamespace(usage=None)) == (0, 0, 0)


# ------------------------------------------------------------- usage stats


async def test_usage_stats_accumulate():
    stats = UsageStats(redis=fakeaioredis.FakeRedis(decode_responses=True))
    resp = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=800),
        )
    )
    await stats.record_response(resp)
    await stats.record_response(resp)
    snap = await stats.snapshot()
    assert snap["calls"] == 2
    assert snap["prompt_tokens"] == 2000
    assert snap["cached_prompt_tokens"] == 1600
    assert snap["completion_tokens"] == 100


async def test_usage_stats_skip_empty_and_never_raise():
    fake = fakeaioredis.FakeRedis(decode_responses=True)
    stats = UsageStats(redis=fake)
    await stats.record_response(SimpleNamespace())  # no usage → no write
    assert await stats.snapshot() == {}
    broken = UsageStats(redis=_BrokenRedis())
    resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1))
    await broken.record_response(resp)  # must not raise
    assert await broken.snapshot() == {}


# ------------------------------------------- recording through the LLM client


class _Draft(BaseModel):
    ok: bool


async def test_structured_completion_records_usage(monkeypatch):
    stats = UsageStats(redis=fakeaioredis.FakeRedis(decode_responses=True))
    usage_mod.set_usage_stats(stats)

    async def fake_acompletion(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"ok": True})))],
            usage=SimpleNamespace(
                prompt_tokens=1200,
                completion_tokens=30,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1100),
            ),
        )

    monkeypatch.setattr(client_mod.litellm, "acompletion", fake_acompletion)
    try:
        parsed, model = await client_mod.structured_completion(
            messages=[{"role": "user", "content": "hi"}],
            response_model=_Draft,
            trace_name="test",
            prompt_version="test_v1",
        )
        assert parsed.ok is True
        snap = await stats.snapshot()
        assert snap == {
            "calls": 1,
            "prompt_tokens": 1200,
            "cached_prompt_tokens": 1100,
            "completion_tokens": 30,
        }
    finally:
        usage_mod.set_usage_stats(None)


# ------------------------------------------------------------- endpoint


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


HEADERS = {"X-Internal-Token": "test-internal-token"}


async def test_cache_endpoint_token_guard(ai_client):
    assert (await ai_client.get("/internal/costs/cache")).status_code == 403


async def test_cache_endpoint_rollup(ai_client, cache, monkeypatch):
    monkeypatch.setattr(semcache_mod, "_cache", cache)
    stats = UsageStats(redis=fakeaioredis.FakeRedis(decode_responses=True))
    usage_mod.set_usage_stats(stats)
    try:
        # 1 store, 1 exact hit, 1 semantic hit, 2 misses → hit rate 0.5
        await cache.put("is the dosa vegan?", E_A, RESPONSE)
        await cache.get("is the dosa vegan?", E_B)
        await cache.get("dosa — vegan or not?", E_A_CLOSE)
        await cache.get("what are your hours?", E_B)
        await cache.get("do you deliver?", E_B)
        resp = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=40,
                prompt_tokens_details=SimpleNamespace(cached_tokens=750),
            )
        )
        await stats.record_response(resp)

        r = await ai_client.get("/internal/costs/cache", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["semcache"]["lookups"] == 4
        assert body["semcache"]["hit_rate"] == pytest.approx(0.5)
        assert body["semcache"]["stores"] == 1
        assert body["prompt_cache"]["cached_share"] == pytest.approx(0.75)
        assert body["prompt_cache"]["calls"] == 1
        assert body["semcache_threshold"] == config.get_settings().semcache_threshold
    finally:
        usage_mod.set_usage_stats(None)
        semcache_mod._cache = None


async def test_cache_endpoint_degrades_to_zeros(ai_client, monkeypatch):
    """Redis outage → zeroed stats, never a 5xx (cost of observability is 0)."""
    monkeypatch.setattr(semcache_mod, "_cache", SemanticCache(redis=_BrokenRedis()))
    usage_mod.set_usage_stats(UsageStats(redis=_BrokenRedis()))
    try:
        r = await ai_client.get("/internal/costs/cache", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["semcache"]["lookups"] == 0
        assert body["prompt_cache"]["cached_share"] == 0.0
    finally:
        usage_mod.set_usage_stats(None)
        semcache_mod._cache = None
