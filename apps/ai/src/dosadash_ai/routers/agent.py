"""Internal order-agent endpoint (api/bot → ai).

POST /internal/agent/chat — one conversational turn on the LangGraph order
agent. X-Internal-Token guarded; the graph redacts PII before any provider
call (Hard Rule 8) and DB-validates every drafted item (Hard Rule 2).
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.graph import run_turn
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
