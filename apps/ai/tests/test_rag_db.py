"""Ingestion + hybrid search against real PostgreSQL (skip without DB).

Embeddings are faked deterministically (conftest.fake_embedding) — vector
similarity behaves like a bag-of-words model, FTS is the real thing.
"""

import pytest
from conftest import KNOWLEDGE_DIR, fake_embed_texts, fake_embedding
from sqlalchemy import func, select

from dosadash_ai.rag import ingest as ingest_mod
from dosadash_ai.rag.chunking import load_knowledge_dir
from dosadash_ai.rag.ingest import ingest_chunks
from dosadash_ai.rag.models import RagChunk
from dosadash_ai.rag.search import hybrid_search, rrf_fuse


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    monkeypatch.setattr(ingest_mod, "embed_texts", fake_embed_texts)


async def _ingest_corpus(session):
    chunks = load_knowledge_dir(KNOWLEDGE_DIR)
    return chunks, await ingest_chunks(session, chunks)


# ------------------------------------------------------------------ ingestion


async def test_ingest_then_reingest_is_incremental(rag_session):
    chunks, first = await _ingest_corpus(rag_session)
    assert first.embedded == len(chunks) > 0
    assert first.deleted == 0

    second = await ingest_chunks(rag_session, chunks)
    assert second.embedded == 0  # nothing changed → no provider calls
    assert second.unchanged == len(chunks)

    count = await rag_session.scalar(select(func.count()).select_from(RagChunk))
    assert count == len(chunks)


async def test_removed_docs_are_deleted(rag_session):
    chunks, _ = await _ingest_corpus(rag_session)
    kept = [c for c in chunks if c.doc_path != "faq.md"]
    report = await ingest_chunks(rag_session, kept)
    assert report.deleted == len(chunks) - len(kept) > 0
    remaining = set(await rag_session.scalars(select(RagChunk.doc_path).distinct()))
    assert "faq.md" not in remaining


async def test_changed_chunk_is_reembedded(rag_session):
    chunks, _ = await _ingest_corpus(rag_session)
    import dataclasses

    edited = list(chunks)
    edited[0] = dataclasses.replace(chunks[0], content=chunks[0].content + "\n\nEdited.")
    report = await ingest_chunks(rag_session, edited)
    assert report.embedded == 1
    assert report.unchanged == len(chunks) - 1


# --------------------------------------------------------------------- search


def test_rrf_fusion_ranks_agreement_highest():
    scores = rrf_fuse([[1, 2, 3], [2, 1, 4]])
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert max(scores, key=scores.get) in (1, 2)
    assert scores[4] < scores[2]


async def test_cancellation_policy_query_hits_policies(rag_session):
    await _ingest_corpus(rag_session)
    query = "Can I cancel my order after it is confirmed?"
    results = await hybrid_search(rag_session, query, fake_embedding(query), top_k=4)
    assert results, "no chunks retrieved"
    top_docs = [r.chunk.doc_path for r in results]
    assert "policies.md" in top_docs
    best_policy = next(r for r in results if r.chunk.doc_path == "policies.md")
    assert "cancel" in best_policy.chunk.content.lower()


async def test_vegan_dosa_query_hits_menu_guide_or_allergens(rag_session):
    await _ingest_corpus(rag_session)
    query = "which dosa is vegan without dairy"
    results = await hybrid_search(rag_session, query, fake_embedding(query), top_k=4)
    assert {r.chunk.doc_path for r in results} & {"menu-guide/dosas.md", "allergens.md"}


async def test_vector_only_still_returns_results(rag_session):
    """FTS-silent query (no English token overlap) → vector ranking carries it."""
    await _ingest_corpus(rag_session)
    query = "zzq unknownword"
    results = await hybrid_search(rag_session, query, fake_embedding(query), top_k=3)
    assert len(results) == 3  # vector scan always ranks the corpus


async def test_search_returns_scores_descending(rag_session):
    await _ingest_corpus(rag_session)
    query = "refund to original payment method"
    results = await hybrid_search(rag_session, query, fake_embedding(query), top_k=6)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
