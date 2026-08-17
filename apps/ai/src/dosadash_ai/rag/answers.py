"""Grounded, cited answers over the knowledge base.

Pipeline: redact (Hard Rule 8) → embed → hybrid RRF retrieval → one
structured LLM call that both reranks (`used_chunks`) and answers — folding
the rerank into the answer call halves cost/latency at this corpus size.
Citations are mapped server-side from `used_chunks` back to document
provenance; the model never fabricates a citation shape (Hard Rule 3).
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.llm.client import embed_texts, structured_completion
from dosadash_ai.llm.semcache import get_semcache
from dosadash_ai.prompts import load_prompt
from dosadash_ai.rag.search import ScoredChunk, hybrid_search
from dosadash_ai.redaction import redact_phones
from dosadash_shared import (
    RAG_ANSWER_PROMPT_VERSION,
    RagAnswerDraft,
    RagAnswerResponse,
    RagCitation,
)

_EMPTY_CORPUS_ANSWER = (
    "Sorry, I don't have that information yet — please contact support and "
    "we'll help you right away."
)


def build_messages(question: str, scored: list[ScoredChunk]) -> list[dict[str, str]]:
    """System prompt from the versioned file + question and numbered context."""
    payload = {
        "question": question,
        "context": [
            {"id": i, "heading": s.chunk.heading, "content": s.chunk.content}
            for i, s in enumerate(scored, start=1)
        ],
    }
    return [
        {"role": "system", "content": load_prompt(RAG_ANSWER_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def map_citations(draft: RagAnswerDraft, scored: list[ScoredChunk]) -> list[RagCitation]:
    """1-based used_chunks → deduped provenance; invalid ids are dropped."""
    if draft.not_found:
        return []
    citations: list[RagCitation] = []
    seen: set[tuple[str, str]] = set()
    for index in draft.used_chunks:
        if not 1 <= index <= len(scored):
            continue  # model hallucinated an id — drop, never invent provenance
        chunk = scored[index - 1].chunk
        key = (chunk.doc_path, chunk.heading)
        if key not in seen:
            seen.add(key)
            citations.append(
                RagCitation(doc_path=chunk.doc_path, title=chunk.title, heading=chunk.heading)
            )
    return citations


async def answer_question(
    session: AsyncSession,
    query: str,
    *,
    top_k: int = 6,
    session_id: str | None = None,
    user_id: str | None = None,
) -> RagAnswerResponse:
    """Answer a knowledge question with citations. May raise LLMError.

    Semantic cache (Phase 4): the query embedding is computed for retrieval
    anyway, so a cache lookup costs zero extra provider calls. Only this
    stateless Q&A path is cached — never agent turns. The cache is flushed
    on menu events and knowledge re-ingest (Hard Rule 4)."""
    question = redact_phones(query)
    [query_embedding] = await embed_texts([question], trace_name="rag.answer.embed")

    cached = await get_semcache().get(question, query_embedding)
    if cached is not None:
        return RagAnswerResponse.model_validate({**cached, "cached": True})

    scored = await hybrid_search(session, question, query_embedding, top_k=top_k)
    if not scored:  # empty corpus / nothing indexed — don't waste an LLM call
        return RagAnswerResponse(
            answer=_EMPTY_CORPUS_ANSWER, citations=[], not_found=True, model=""
        )

    draft, model = await structured_completion(
        messages=build_messages(question, scored),
        response_model=RagAnswerDraft,
        trace_name="rag.answer",
        prompt_version=RAG_ANSWER_PROMPT_VERSION,
        session_id=session_id,
        user_id=user_id,
    )
    citations = map_citations(draft, scored)
    # Faithfulness backstop: an affirmative answer with zero verifiable
    # citations is not trustworthy — degrade to not_found rather than emit
    # an ungrounded claim.
    if not citations and not draft.not_found:
        return RagAnswerResponse(
            answer=_EMPTY_CORPUS_ANSWER, citations=[], not_found=True, model=model
        )
    response = RagAnswerResponse(
        answer=draft.answer, citations=citations, not_found=draft.not_found, model=model
    )
    # Cache only grounded, affirmative answers: refusals are cheap to
    # recompute and may become answerable after the next ingest.
    if not response.not_found:
        await get_semcache().put(question, query_embedding, response.model_dump(exclude={"cached"}))
    return response
