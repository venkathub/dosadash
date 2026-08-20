"""Dish-photo QC tests — VLM mocked, verdict layer exercised for real."""

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm.client import LLMError
from dosadash_ai.qc import extract as qc_extract
from dosadash_ai.qc.extract import build_messages, qc_dish_photo
from dosadash_shared import DishQCExtraction, DishQCIn

AUDIO_FREE_IMG = "aGVsbG8gZGlzaCBwaG90bw=="  # any base64 — never decoded in tests


def _request(**overrides) -> DishQCIn:
    payload = {
        "image_base64": AUDIO_FREE_IMG,
        "mime_type": "image/jpeg",
        "expected_dishes": ["Masala Dosa", "Filter Coffee"],
        "session_id": "kds:1",
    }
    payload.update(overrides)
    return DishQCIn(**payload)


def test_build_messages_carries_order_context_and_image():
    messages = build_messages(_request())
    assert "dish_qc" in messages[0]["content"].lower() or "inspector" in messages[0]["content"]
    user_parts = messages[1]["content"]
    assert "Masala Dosa, Filter Coffee" in user_parts[0]["text"]
    assert user_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_happy_path_pass(monkeypatch):
    async def fake_completion(**kwargs):
        assert kwargs["trace_name"] == "dish_qc"
        assert kwargs["models"] == ["gpt-4o-mini", "gemini/gemini-1.5-flash"]  # vision only
        return (
            DishQCExtraction(
                is_food_photo=True,
                dishes_seen=["masala dosa", "filter coffee"],
                presentation_issues=[],
                confidence=0.9,
            ),
            "gpt-4o-mini",
        )

    monkeypatch.setattr(qc_extract, "structured_completion", fake_completion)
    result = await qc_dish_photo(_request())
    assert result.verdict == "PASS"
    assert result.model == "gpt-4o-mini"
    assert result.missing == []


async def test_chain_failure_is_unreadable_not_5xx(monkeypatch):
    async def boom(**kwargs):
        raise LLMError("all vision models down")

    monkeypatch.setattr(qc_extract, "structured_completion", boom)
    result = await qc_dish_photo(_request())
    assert result.verdict == "UNREADABLE"
    assert result.error is not None
    assert result.missing == ["Masala Dosa", "Filter Coffee"]


# ------------------------------------------------------------------ endpoint


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
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


async def test_qc_requires_internal_token(ai_client):
    resp = await ai_client.post(
        "/internal/qc/dish",
        json={
            "image_base64": AUDIO_FREE_IMG,
            "mime_type": "image/jpeg",
            "expected_dishes": ["Masala Dosa"],
        },
    )
    assert resp.status_code == 403


async def test_qc_endpoint_mismatch(ai_client, monkeypatch):
    async def fake_completion(**kwargs):
        return (
            DishQCExtraction(
                is_food_photo=True,
                dishes_seen=["idli"],
                presentation_issues=[],
                confidence=0.9,
            ),
            "gpt-4o-mini",
        )

    monkeypatch.setattr(qc_extract, "structured_completion", fake_completion)
    resp = await ai_client.post(
        "/internal/qc/dish",
        json={
            "image_base64": AUDIO_FREE_IMG,
            "mime_type": "image/jpeg",
            "expected_dishes": ["Chicken Biryani"],
        },
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "MISMATCH"
    assert body["missing"] == ["Chicken Biryani"]
    assert body["unexpected"] == ["idli"]


async def test_qc_endpoint_validates_input(ai_client):
    resp = await ai_client.post(
        "/internal/qc/dish",
        json={"image_base64": AUDIO_FREE_IMG, "mime_type": "image/gif", "expected_dishes": ["x"]},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422
    resp = await ai_client.post(
        "/internal/qc/dish",
        json={"image_base64": AUDIO_FREE_IMG, "mime_type": "image/jpeg", "expected_dishes": []},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422
