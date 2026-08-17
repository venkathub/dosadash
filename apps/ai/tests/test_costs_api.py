"""/internal/costs/daily: token guard, Langfuse mapping, cache, degradation."""

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.routers import costs as costs_mod

LANGFUSE_PAYLOAD = {
    "data": [
        {
            "date": "2026-08-17",
            "countTraces": 120,
            "countObservations": 240,
            "totalCost": 0.42,
            "usage": [
                {
                    "model": "gpt-4o-mini",
                    "inputUsage": 900_000,
                    "outputUsage": 60_000,
                    "totalCost": 0.40,
                    "countObservations": 230,
                },
                {
                    "model": "groq/llama-3.3-70b-versatile",
                    "inputUsage": 12_000,
                    "outputUsage": 2_000,
                    "totalCost": 0.02,
                    "countObservations": 10,
                },
            ],
        },
        {
            "date": "2026-08-16",
            "countTraces": 80,
            "countObservations": 160,
            "totalCost": 0.30,
            "usage": [],
        },
    ],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    config.get_settings.cache_clear()
    costs_mod._cache.clear()
    yield
    config.get_settings.cache_clear()
    costs_mod._cache.clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def fetch_calls(monkeypatch):
    calls = {"n": 0, "payload": LANGFUSE_PAYLOAD}

    async def fake_fetch(host, auth, days):
        calls["n"] += 1
        calls["host"] = host
        calls["auth"] = auth
        return calls["payload"]

    monkeypatch.setattr(costs_mod, "_fetch_daily_metrics", fake_fetch)
    return calls


HEADERS = {"X-Internal-Token": "test-internal-token"}


async def test_token_guard(ai_client, fetch_calls):
    assert (await ai_client.get("/internal/costs/daily")).status_code == 403
    bad = {"X-Internal-Token": "nope"}
    assert (await ai_client.get("/internal/costs/daily", headers=bad)).status_code == 403


async def test_unconfigured_langfuse_reports_not_configured(ai_client, monkeypatch, fetch_calls):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    resp = await ai_client.get("/internal/costs/daily", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["days"] == []
    assert fetch_calls["n"] == 0  # no Langfuse call without keys


async def test_daily_costs_normalized(ai_client, fetch_calls):
    resp = await ai_client.get("/internal/costs/daily?days=14", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["total_cost_usd"] == pytest.approx(0.72)
    assert [d["date"] for d in body["days"]] == ["2026-08-17", "2026-08-16"]
    top = body["days"][0]
    assert top["traces"] == 120
    assert top["models"][0]["model"] == "gpt-4o-mini"
    assert top["models"][0]["cost_usd"] == pytest.approx(0.40)
    assert top["models"][0]["input_tokens"] == 900_000
    assert fetch_calls["auth"] == ("pk-test", "sk-test")


async def test_short_ttl_cache_prevents_hammering(ai_client, fetch_calls):
    await ai_client.get("/internal/costs/daily?days=14", headers=HEADERS)
    await ai_client.get("/internal/costs/daily?days=14", headers=HEADERS)
    assert fetch_calls["n"] == 1  # second hit served from the 60s cache
    await ai_client.get("/internal/costs/daily?days=7", headers=HEADERS)
    assert fetch_calls["n"] == 2  # different window = different cache key


async def test_langfuse_failure_maps_to_502(ai_client, monkeypatch):
    async def broken_fetch(host, auth, days):
        raise httpx.ConnectError("langfuse down")

    monkeypatch.setattr(costs_mod, "_fetch_daily_metrics", broken_fetch)
    resp = await ai_client.get("/internal/costs/daily", headers=HEADERS)
    assert resp.status_code == 502


async def test_malformed_rows_degrade_to_zeroes(ai_client, fetch_calls):
    fetch_calls["payload"] = {"data": [{"date": "2026-08-17", "usage": [{}]}]}
    resp = await ai_client.get("/internal/costs/daily", headers=HEADERS)
    assert resp.status_code == 200
    day = resp.json()["days"][0]
    assert day["cost_usd"] == 0.0
    assert day["models"][0]["model"] == "unknown"
