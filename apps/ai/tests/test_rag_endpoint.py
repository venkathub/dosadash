"""/internal/rag/search endpoint: token guard, redaction, response shape."""

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.db import get_session
from dosadash_ai.llm.client import LLMError
from dosadash_ai.rag.models import RagChunk
from dosadash_ai.rag.search import ScoredChunk
from dosadash_ai.routers import rag as rag_router


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _fake_chunk() -> ScoredChunk:
    chunk = RagChunk(
        id=1,
        doc_path="policies.md",
        doc_type="policy",
        title="Ordering, Cancellation & Refund Policies",
        tags=["policy"],
        heading="Policies › Cancellation policy",
        chunk_index=1,
        content="You may cancel while PLACED.",
        content_hash="x" * 64,
    )
    return ScoredChunk(chunk=chunk, score=0.032)


@pytest.fixture
async def ai_client(monkeypatch):
    from dosadash_ai.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_search_requires_internal_token(ai_client):
    resp = await ai_client.post("/internal/rag/search", json={"query": "vegan dosa"})
    assert resp.status_code == 403
    resp = await ai_client.post(
        "/internal/rag/search",
        json={"query": "vegan dosa"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 403


async def test_search_happy_path_redacts_phone(ai_client, monkeypatch):
    embedded: list[str] = []

    async def fake_embed(texts, **_):
        embedded.extend(texts)
        return [[0.0] * 5 for _ in texts]

    async def fake_search(session, query, embedding, *, top_k):
        assert top_k == 3
        return [_fake_chunk()]

    monkeypatch.setattr(rag_router, "embed_texts", fake_embed)
    monkeypatch.setattr(rag_router, "hybrid_search", fake_search)

    resp = await ai_client.post(
        "/internal/rag/search",
        json={"query": "cancel order for +91 98765 43210 please", "top_k": 3},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "[phone]" in body["query"] and "98765" not in body["query"]  # Hard Rule 8
    assert "98765" not in embedded[0]  # provider never sees the phone
    assert body["chunks"][0]["doc_path"] == "policies.md"
    assert body["chunks"][0]["heading"] == "Policies › Cancellation policy"
    assert body["chunks"][0]["score"] > 0


async def test_search_502_when_embedding_fails(ai_client, monkeypatch):
    async def fake_embed(texts, **_):
        raise LLMError("providers down")

    monkeypatch.setattr(rag_router, "embed_texts", fake_embed)
    resp = await ai_client.post(
        "/internal/rag/search",
        json={"query": "vegan dosa"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 502


async def test_search_validates_input(ai_client):
    resp = await ai_client.post(
        "/internal/rag/search",
        json={"query": "", "top_k": 3},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422
    resp = await ai_client.post(
        "/internal/rag/search",
        json={"query": "hi", "top_k": 99},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422
