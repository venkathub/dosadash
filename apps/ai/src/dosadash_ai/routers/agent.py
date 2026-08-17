"""Internal order-agent endpoints (api/bot → ai).

POST /internal/agent/chat        — one turn, JSON response
POST /internal/agent/chat/stream — same turn as SSE: reply deltas, then the
                                   guardrail-validated final response

X-Internal-Token guarded; the graph redacts PII before any provider call
(Hard Rule 8) and DB-validates every drafted item (Hard Rule 2).
"""

import json
import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.graph import run_turn
from dosadash_ai.agent.streaming import stream_turn
from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.llm import LLMError
from dosadash_shared import AgentChatRequest, AgentChatResponse

router = APIRouter(prefix="/internal/agent", tags=["internal:agent"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/chat", response_model=AgentChatResponse)
async def chat(
    req: AgentChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> AgentChatResponse:
    _check_internal_token(x_internal_token)
    try:
        return await run_turn(session, req)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM chain failed: {exc}") from exc


@router.post("/chat/stream")
async def chat_stream(
    req: AgentChatRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> StreamingResponse:
    _check_internal_token(x_internal_token)

    async def sse() -> AsyncIterator[str]:
        async for event in stream_turn(session, req):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
