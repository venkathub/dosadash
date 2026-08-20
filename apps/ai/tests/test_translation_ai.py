"""Translation chain tests (Phase 7): endpoint auth, chunking, and the
guardrail behaviour end-to-end — litellm is always mocked (CI never calls
providers). Pure sanitizer cases live in evals/suites/test_translation_assets.py."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm import client as llm_client
from dosadash_ai.routers.translation import build_messages
from dosadash_shared import (
    TRANSLATION_CHUNK_SIZE,
    MenuTranslationRequest,
    TranslationSourceItem,
)

TRANSLATE = "/internal/translate/menu"


class FakeResponse:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


def _echo_batch(kwargs, extra: list[dict] | None = None) -> FakeResponse:
    """Build a valid Tamil batch for whatever items the user message carried."""
    payload = json.loads(kwargs["messages"][-1]["content"])
    translations = [
        {
            "item_id": item["item_id"],
            "name": f"தமிழ் {item['item_id']}",
            "description": None,
            "category_label": "டிஃபின்",
        }
        for item in payload["items"]
    ]
    return FakeResponse(json.dumps({"translations": translations + (extra or [])}))


def _req(n: int = 2) -> MenuTranslationRequest:
    return MenuTranslationRequest(
        lang="ta",
        items=[
            TranslationSourceItem(item_id=i, name=f"Dish {i}", description=None, category="Tiffin")
            for i in range(1, n + 1)
        ],
    )


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
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_translate_requires_internal_token(ai_client):
    body = _req().model_dump(mode="json")
    assert (await ai_client.post(TRANSLATE, json=body)).status_code == 403
    resp = await ai_client.post(TRANSLATE, json=body, headers={"X-Internal-Token": "wrong"})
    assert resp.status_code == 403


async def test_unsupported_language_rejected(ai_client):
    resp = await ai_client.post(
        TRANSLATE,
        json={"lang": "fr", "items": [{"item_id": 1, "name": "Dosa", "category": "Dosa"}]},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422


async def test_translate_end_to_end_with_guardrail(ai_client, monkeypatch):
    """Valid drafts survive; a hallucinated item_id in the same output is
    silently dropped by the guardrail."""

    async def fake_acompletion(**kwargs):
        ghost = {"item_id": 999, "name": "போலி உணவு", "description": None, "category_label": None}
        return _echo_batch(kwargs, extra=[ghost])

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        TRANSLATE,
        json=_req().model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["item_id"] for t in body["translations"]] == [1, 2]
    assert body["rejected"] == []
    assert body["model"] == "gpt-4o-mini"
    assert body["prompt_version"] == "menu_translation_v1"


async def test_batch_fans_out_in_chunks(ai_client, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"]))
        return _echo_batch(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    n = TRANSLATION_CHUNK_SIZE + 2
    resp = await ai_client.post(
        TRANSLATE,
        json=_req(n).model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200
    assert len(calls) == 2  # one full chunk + the remainder
    assert len(calls[0]["items"]) == TRANSLATION_CHUNK_SIZE
    assert len(resp.json()["translations"]) == n


async def test_one_dead_chunk_does_not_sink_the_batch(ai_client, monkeypatch):
    attempts = {"n": 0}

    async def fake_acompletion(**kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 3:  # first chunk exhausts the whole 3-model chain
            raise RuntimeError("provider down")
        return _echo_batch(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        TRANSLATE,
        json=_req(TRANSLATION_CHUNK_SIZE + 2).model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rejected"]) == TRANSLATION_CHUNK_SIZE  # the dead chunk, per item
    assert all("LLM chain failed" in r["reason"] for r in body["rejected"])
    assert [t["item_id"] for t in body["translations"]] == [
        TRANSLATION_CHUNK_SIZE + 1,
        TRANSLATION_CHUNK_SIZE + 2,
    ]


async def test_502_when_every_chunk_fails(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        TRANSLATE,
        json=_req().model_dump(mode="json"),
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 502


def test_build_messages_uses_versioned_prompt():
    messages = build_messages("ta", _req().items)
    assert messages[0]["role"] == "system"
    assert "menu localizer" in messages[0]["content"]
    assert '"category_label"' in messages[0]["content"]  # schema in prompt
    payload = json.loads(messages[1]["content"])
    assert payload["target_language"] == "Tamil"
    assert payload["items"][0]["name"] == "Dish 1"
