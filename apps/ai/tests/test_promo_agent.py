"""Promo agent tests — mining/stats faked, LLM mocked, guardrail real."""

from decimal import Decimal

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm.client import LLMError
from dosadash_ai.promo import agent as promo_agent
from dosadash_ai.promo.agent import suggest_promos
from dosadash_shared import CouponType, MinedPair, PromoDraftBatch, PromoStats

PAIRS = [
    MinedPair(
        item_ids=[3, 12],
        names=["Masala Dosa", "Filter Coffee"],
        parts_total=Decimal("180.00"),
        times_ordered=41,
    )
]
STATS = PromoStats(slow_day="Tuesday", median_aov=Decimal("310.00"), existing_codes=["DOSA10"])


@pytest.fixture(autouse=True)
def _fake_mining(monkeypatch):
    async def fake_pairs(session):
        return list(PAIRS)

    async def fake_stats(session):
        return STATS

    monkeypatch.setattr(promo_agent, "mine_pairs", fake_pairs)
    monkeypatch.setattr(promo_agent, "gather_stats", fake_stats)


async def test_happy_path_sanitized(monkeypatch):
    async def fake_completion(**kwargs):
        assert kwargs["trace_name"] == "promo_suggest"
        batch = PromoDraftBatch.model_validate(
            {
                "combos": [
                    # valid pair but greedy price → clamped to 97%
                    {
                        "item_ids": [3, 12],
                        "name": "Kaapi Combo",
                        "price": "500",
                        "rationale": "41×",
                    },
                    # hallucinated pair → dropped
                    {"item_ids": [77, 78], "name": "Ghost", "price": "100", "rationale": "x"},
                ],
                "coupons": [
                    {
                        "code": "dosa10",  # collides with existing → suffixed
                        "type": "PCT",
                        "value": "80",  # → clamped to 30
                        "max_discount": None,  # → defaulted
                        "min_subtotal": None,
                        "description": "Big discount",
                        "rationale": "slow Tuesday",
                    }
                ],
            }
        )
        return batch, "gpt-4o-mini"

    monkeypatch.setattr(promo_agent, "structured_completion", fake_completion)
    result = await suggest_promos(None)
    assert not result.fallback
    assert [c.name for c in result.combos] == ["Kaapi Combo"]
    assert result.combos[0].price == Decimal("174.60")  # 97% of 180
    coupon = result.coupons[0]
    assert coupon.code == "DOSA102"
    assert coupon.value == Decimal("30.00")
    assert coupon.max_discount is not None
    assert result.model == "gpt-4o-mini"


async def test_llm_failure_deterministic_fallback(monkeypatch):
    async def boom(**kwargs):
        raise LLMError("all models down")

    monkeypatch.setattr(promo_agent, "structured_completion", boom)
    result = await suggest_promos(None)
    assert result.fallback
    assert result.model is None
    assert [c.name for c in result.combos] == ["Masala Dosa + Filter Coffee Combo"]
    assert result.combos[0].price == Decimal("162.00")  # 90% of parts
    assert result.coupons[0].type == CouponType.PCT
    assert result.coupons[0].code.startswith("TUE")


# ------------------------------------------------------------------ endpoint


@pytest.fixture
async def ai_client(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    from dosadash_ai.db import get_session
    from dosadash_ai.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    config.get_settings.cache_clear()


async def test_endpoint_requires_token(ai_client):
    resp = await ai_client.post("/internal/promo/suggest")
    assert resp.status_code == 403


async def test_endpoint_fallback_is_200(ai_client, monkeypatch):
    async def boom(**kwargs):
        raise LLMError("down")

    monkeypatch.setattr(promo_agent, "structured_completion", boom)
    resp = await ai_client.post(
        "/internal/promo/suggest",
        headers={"X-Internal-Token": "test-internal-token", "X-Admin-User-Id": "9"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fallback"] is True
    assert len(body["combos"]) == 1
