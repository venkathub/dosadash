"""Answer service unit tests — LLM and retrieval mocked, logic real."""

import json

import pytest

from dosadash_ai.rag import answers as answers_mod
from dosadash_ai.rag.answers import answer_question, build_messages, map_citations
from dosadash_ai.rag.models import RagChunk
from dosadash_ai.rag.search import ScoredChunk
from dosadash_shared import RAG_ANSWER_PROMPT_VERSION, RagAnswerDraft


def _chunk(doc_path: str, heading: str, content: str = "text") -> ScoredChunk:
    return ScoredChunk(
        chunk=RagChunk(
            id=hash((doc_path, heading)) % 10_000,
            doc_path=doc_path,
            doc_type="policy",
            title="Policies",
            tags=[],
            heading=heading,
            chunk_index=0,
            content=content,
            content_hash="x" * 64,
        ),
        score=0.03,
    )


SCORED = [
    _chunk("policies.md", "Policies › Cancellation policy", "Cancel while PLACED."),
    _chunk("policies.md", "Policies › Refund policy", "Refund to original method."),
    _chunk("faq.md", "FAQ › Delivery", "Chennai pincodes only."),
]


# ------------------------------------------------------------ build_messages


def test_build_messages_numbers_context_and_uses_versioned_prompt():
    messages = build_messages("can I cancel?", SCORED)
    assert messages[0]["role"] == "system"
    assert "not_found" in messages[0]["content"]  # the versioned prompt file
    payload = json.loads(messages[1]["content"])
    assert payload["question"] == "can I cancel?"
    assert [c["id"] for c in payload["context"]] == [1, 2, 3]
    assert payload["context"][0]["heading"] == "Policies › Cancellation policy"


# ------------------------------------------------------------- map_citations


def test_map_citations_maps_dedupes_and_drops_invalid():
    draft = RagAnswerDraft(answer="a", used_chunks=[1, 1, 99, 3, 0])
    citations = map_citations(draft, SCORED)
    assert [c.heading for c in citations] == [
        "Policies › Cancellation policy",
        "FAQ › Delivery",
    ]  # 99 and 0 dropped, duplicate 1 deduped


def test_map_citations_empty_when_not_found():
    draft = RagAnswerDraft(answer="don't know", used_chunks=[1], not_found=True)
    assert map_citations(draft, SCORED) == []


# ----------------------------------------------------------- answer_question


@pytest.fixture
def patched(monkeypatch):
    """Patch retrieval + embedding; LLM behavior set per-test via `draft`."""
    state = {"draft": RagAnswerDraft(answer="Yes, while PLACED.", used_chunks=[1]), "calls": 0}

    async def fake_embed(texts, **_):
        return [[0.0] * 3 for _ in texts]

    async def fake_search(session, query, embedding, *, top_k):
        return state.get("scored", SCORED)

    async def fake_completion(**kwargs):
        state["calls"] += 1
        state["kwargs"] = kwargs
        return state["draft"], "gpt-4o-mini"

    monkeypatch.setattr(answers_mod, "embed_texts", fake_embed)
    monkeypatch.setattr(answers_mod, "hybrid_search", fake_search)
    monkeypatch.setattr(answers_mod, "structured_completion", fake_completion)
    return state


async def test_answer_happy_path(patched):
    resp = await answer_question(None, "can I cancel my order?", session_id="s1")
    assert resp.answer == "Yes, while PLACED."
    assert resp.not_found is False
    assert resp.model == "gpt-4o-mini"
    assert resp.prompt_version == RAG_ANSWER_PROMPT_VERSION
    assert [c.doc_path for c in resp.citations] == ["policies.md"]
    assert patched["kwargs"]["session_id"] == "s1"
    assert patched["kwargs"]["prompt_version"] == RAG_ANSWER_PROMPT_VERSION


async def test_answer_redacts_phone_before_llm(patched):
    await answer_question(None, "cancel order for +91 98765 43210")
    payload = json.loads(patched["kwargs"]["messages"][1]["content"])
    assert "98765" not in payload["question"]  # Hard Rule 8
    assert "[phone]" in payload["question"]


async def test_not_found_clears_citations(patched):
    patched["draft"] = RagAnswerDraft(answer="No idea, contact support.", not_found=True)
    resp = await answer_question(None, "do you sell pizza?")
    assert resp.not_found is True
    assert resp.citations == []


async def test_empty_retrieval_skips_llm(patched):
    patched["scored"] = []
    resp = await answer_question(None, "anything?")
    assert resp.not_found is True
    assert resp.model == ""
    assert patched["calls"] == 0  # no LLM call on empty corpus


async def test_ungrounded_answer_degrades_to_not_found(patched):
    """Affirmative answer citing nothing valid → refuse rather than emit."""
    patched["draft"] = RagAnswerDraft(answer="Sure, free biryani!", used_chunks=[99])
    resp = await answer_question(None, "free biryani?")
    assert resp.not_found is True
    assert "biryani" not in resp.answer.lower()
    assert resp.citations == []
