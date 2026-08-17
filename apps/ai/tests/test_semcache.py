"""Semantic cache units (fakeredis): hit/miss semantics, TTL, bounded
candidates, invalidation, and the never-raise contract."""

import pytest
from fakeredis import aioredis as fakeaioredis

from dosadash_ai.config import get_settings
from dosadash_ai.llm.semcache import SemanticCache, cosine

E_A = [1.0, 0.0, 0.0]
E_A_CLOSE = [0.999, 0.04, 0.0]  # cosine ~0.999 with E_A
E_B = [0.0, 1.0, 0.0]  # orthogonal — must miss

RESPONSE = {
    "answer": "Ghee Roast Dosa contains ghee (dairy) — not vegan.",
    "citations": [{"doc_path": "allergens.md", "title": "Allergens", "heading": "Dairy"}],
    "not_found": False,
    "model": "gpt-4o-mini",
    "prompt_version": "rag_answer_v1",
}


@pytest.fixture
def cache(monkeypatch):
    monkeypatch.setattr(get_settings(), "semcache_enabled", True)
    return SemanticCache(redis=fakeaioredis.FakeRedis(decode_responses=True))


async def test_miss_on_empty_cache(cache):
    assert await cache.get("is the dosa vegan?", E_A) is None


async def test_exact_hit_ignores_case_and_whitespace(cache):
    await cache.put("Is the Ghee Roast  Dosa vegan?", E_A, RESPONSE)
    hit = await cache.get("is the ghee roast dosa VEGAN?", E_B)  # embedding irrelevant
    assert hit == RESPONSE


async def test_semantic_hit_above_threshold(cache):
    await cache.put("is the ghee roast dosa vegan?", E_A, RESPONSE)
    hit = await cache.get("ghee roast dosa — vegan or not?", E_A_CLOSE)
    assert hit == RESPONSE


async def test_semantic_miss_below_threshold(cache):
    await cache.put("is the ghee roast dosa vegan?", E_A, RESPONSE)
    assert await cache.get("what are your delivery hours?", E_B) is None


async def test_flush_empties_cache(cache):
    await cache.put("q1", E_A, RESPONSE)
    await cache.put("q2", E_B, RESPONSE)
    removed = await cache.flush()
    assert removed == 2
    assert await cache.get("q1", E_A) is None
    assert await cache.get("q2", E_B) is None


async def test_candidate_list_is_bounded(cache, monkeypatch):
    monkeypatch.setattr(get_settings(), "semcache_max_candidates", 3)
    for i in range(6):
        await cache.put(f"question {i}", [float(i), 1.0, 0.0], RESPONSE)
    keys = await cache._client().lrange("semcache:rag:keys", 0, -1)
    assert len(keys) == 3


async def test_entries_carry_ttl(cache):
    await cache.put("ttl question", E_A, RESPONSE)
    key = (await cache._client().lrange("semcache:rag:keys", 0, 0))[0]
    ttl = await cache._client().ttl(key)
    assert 0 < ttl <= get_settings().semcache_ttl_seconds


async def test_disabled_flag_bypasses_cache(cache, monkeypatch):
    await cache.put("q", E_A, RESPONSE)
    monkeypatch.setattr(get_settings(), "semcache_enabled", False)
    assert await cache.get("q", E_A) is None


async def test_redis_failure_degrades_to_miss(monkeypatch):
    class ExplodingRedis:
        def __getattr__(self, name):
            raise ConnectionError("redis down")

    monkeypatch.setattr(get_settings(), "semcache_enabled", True)
    broken = SemanticCache(redis=ExplodingRedis())
    assert await broken.get("q", E_A) is None  # never raises
    await broken.put("q", E_A, RESPONSE)  # never raises
    assert await broken.flush() == 0  # never raises


def test_cosine_properties():
    assert cosine(E_A, E_A) == pytest.approx(1.0)
    assert cosine(E_A, E_B) == pytest.approx(0.0)
    assert cosine(E_A, [0.0, 0.0, 0.0]) == 0.0
    assert cosine(E_A, E_A_CLOSE) > 0.99
