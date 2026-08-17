"""AI service tests: structured_completion chain behavior + the internal
nutrition endpoint — litellm is always mocked (CI never calls providers)."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm import LLMError
from dosadash_ai.llm import client as llm_client
from dosadash_ai.llm.client import structured_completion
from dosadash_ai.routers.nutrition import build_messages
from dosadash_shared import NutritionEstimate, NutritionEstimateRequest

GOOD_JSON = json.dumps(
    {
        "calories_kcal": 320,
        "protein_g": 8,
        "carbs_g": 52,
        "fat_g": 9,
        "fiber_g": 4,
        "per": "serving",
        "confidence": 0.8,
        "notes": "one masala dosa with potato filling",
    }
)


class FakeResponse:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


def _req() -> NutritionEstimateRequest:
    return NutritionEstimateRequest(
        item_name="Masala Dosa",
        category="Dosa",
        description="Crisp dosa with potato masala",
        is_veg=True,
        recipe=[{"name": "idli rice", "qty": "0.2", "unit": "kg"}],
    )


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


# ----------------------------------------------------------- structured chain


async def test_structured_completion_happy_path(monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return FakeResponse(GOOD_JSON)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    parsed, model = await structured_completion(
        messages=[{"role": "user", "content": "hi"}],
        response_model=NutritionEstimate,
        trace_name="t",
        prompt_version="nutrition_v1",
    )
    assert parsed.calories_kcal == 320
    assert model == "gpt-4o-mini"  # first in the chain
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["metadata"]["tags"] == ["nutrition_v1"]


async def test_validation_failure_gets_repair_retry(monkeypatch):
    """First response is invalid JSON-schema-wise → repair message → valid."""
    responses = [FakeResponse('{"calories_kcal": -5}'), FakeResponse(GOOD_JSON)]
    seen_messages = []

    async def fake_acompletion(**kwargs):
        seen_messages.append(kwargs["messages"])
        return responses.pop(0)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    parsed, model = await structured_completion(
        messages=[{"role": "user", "content": "hi"}],
        response_model=NutritionEstimate,
        trace_name="t",
        prompt_version="nutrition_v1",
    )
    assert parsed.protein_g == 8
    assert model == "gpt-4o-mini"  # repaired on the SAME model
    assert "failed schema validation" in seen_messages[1][-1]["content"]


async def test_provider_error_falls_through_chain(monkeypatch):
    attempted = []

    async def fake_acompletion(**kwargs):
        attempted.append(kwargs["model"])
        if kwargs["model"] == "gpt-4o-mini":
            raise RuntimeError("rate limited")
        return FakeResponse(GOOD_JSON)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    _, model = await structured_completion(
        messages=[{"role": "user", "content": "hi"}],
        response_model=NutritionEstimate,
        trace_name="t",
        prompt_version="nutrition_v1",
    )
    assert model == "groq/openai/gpt-oss-120b"
    assert attempted == ["gpt-4o-mini", "groq/openai/gpt-oss-120b"]


async def test_all_models_fail_raises_llm_error(monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    with pytest.raises(LLMError):
        await structured_completion(
            messages=[{"role": "user", "content": "hi"}],
            response_model=NutritionEstimate,
            trace_name="t",
            prompt_version="nutrition_v1",
        )


# -------------------------------------------------------------- http endpoint


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_estimate_requires_internal_token(ai_client):
    resp = await ai_client.post("/internal/nutrition/estimate", json=_req().model_dump(mode="json"))
    assert resp.status_code == 403
    resp = await ai_client.post(
        "/internal/nutrition/estimate",
        json=_req().model_dump(mode="json"),
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 403


async def test_estimate_end_to_end_mocked(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        # the user message must carry the recipe context
        user_payload = json.loads(kwargs["messages"][-1]["content"])
        assert user_payload["recipe"][0]["ingredient"] == "idli rice"
        return FakeResponse(GOOD_JSON)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        "/internal/nutrition/estimate",
        json=_req().model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estimate"]["calories_kcal"] == 320
    assert body["model"] == "gpt-4o-mini"
    assert body["prompt_version"] == "nutrition_v1"


async def test_estimate_502_when_chain_fails(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        "/internal/nutrition/estimate",
        json=_req().model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 502


def test_build_messages_uses_versioned_prompt():
    messages = build_messages(_req())
    assert messages[0]["role"] == "system"
    assert "nutrition analyst" in messages[0]["content"]
    assert '"calories_kcal"' in messages[0]["content"]  # schema in prompt
    assert "Masala Dosa" in messages[1]["content"]


async def test_rate_limit_retries_same_model_before_falling_through(monkeypatch):
    """429s get brief same-model retries (TPM windows clear in seconds) —
    added after a 150-case live gate run brushed OpenAI's TPM ceiling."""
    import litellm

    attempts = []

    async def fake_acompletion(**kwargs):
        attempts.append(kwargs["model"])
        if len(attempts) == 1:
            raise litellm.RateLimitError(
                "tokens per min", llm_provider="openai", model="gpt-4o-mini"
            )
        return FakeResponse(GOOD_JSON)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm_client, "_RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    _, model = await structured_completion(
        messages=[{"role": "user", "content": "hi"}],
        response_model=NutritionEstimate,
        trace_name="t",
        prompt_version="nutrition_v1",
    )
    assert model == "gpt-4o-mini"  # recovered on the SAME model
    assert attempts == ["gpt-4o-mini", "gpt-4o-mini"]


async def test_rate_limit_exhaustion_falls_through_chain(monkeypatch):
    import litellm

    attempts = []

    async def fake_acompletion(**kwargs):
        attempts.append(kwargs["model"])
        if kwargs["model"] == "gpt-4o-mini":
            raise litellm.RateLimitError(
                "tokens per min", llm_provider="openai", model="gpt-4o-mini"
            )
        return FakeResponse(GOOD_JSON)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm_client, "_RATE_LIMIT_BACKOFF_SECONDS", 0.0)
    _, model = await structured_completion(
        messages=[{"role": "user", "content": "hi"}],
        response_model=NutritionEstimate,
        trace_name="t",
        prompt_version="nutrition_v1",
    )
    assert model == "groq/openai/gpt-oss-120b"
    assert attempts.count("gpt-4o-mini") == 3  # initial + 2 rate-limit retries
