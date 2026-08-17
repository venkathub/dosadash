"""Internal RAG endpoints (api/agent → ai): retrieval and grounded answers.

POST /internal/rag/search — hybrid retrieval with provenance
POST /internal/rag/answer — retrieval + structured LLM answer with citations

X-Internal-Token guarded (same pattern as nutrition). Queries are
phone-redacted (Hard Rule 8) before they are embedded or logged.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.llm import LLMError
from dosadash_ai.llm.client import embed_texts
from dosadash_ai.rag.answers import answer_question
from dosadash_ai.rag.search import hybrid_search
from dosadash_ai.redaction import redact_phones
from dosadash_shared import (
    RagAnswerRequest,
    RagAnswerResponse,
    RagChunkOut,
    RagSearchRequest,
    RagSearchResponse,
)

router = APIRouter(prefix="/internal/rag", tags=["internal:rag"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/search", response_model=RagSearchResponse)
async def search(
    req: RagSearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> RagSearchResponse:
    _check_internal_token(x_internal_token)
    query = redact_phones(req.query)
    try:
        [query_embedding] = await embed_texts([query], trace_name="rag.search.embed")
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {exc}") from exc
    scored = await hybrid_search(session, query, query_embedding, top_k=req.top_k)
    return RagSearchResponse(
        query=query,
        chunks=[
            RagChunkOut(
                id=s.chunk.id,
                doc_path=s.chunk.doc_path,
                doc_type=s.chunk.doc_type,
                title=s.chunk.title,
                heading=s.chunk.heading,
                content=s.chunk.content,
                score=s.score,
            )
            for s in scored
        ],
    )


@router.post("/answer", response_model=RagAnswerResponse)
async def answer(
    req: RagAnswerRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> RagAnswerResponse:
    _check_internal_token(x_internal_token)
    try:
        return await answer_question(
            session,
            req.query,
            top_k=req.top_k,
            session_id=req.session_id,
            user_id=req.user_id,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM chain failed: {exc}") from exc
