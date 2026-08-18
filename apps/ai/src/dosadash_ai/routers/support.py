"""Internal support-agent endpoint (Phase 6).

POST /internal/support/chat — X-Internal-Token guarded. The api owns auth
and action execution; this endpoint only reasons (Hard Rule 10 spirit).
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.support.agent import support_turn
from dosadash_shared import SupportAgentRequest, SupportAgentResponse

router = APIRouter(prefix="/internal/support", tags=["internal:support"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/chat", response_model=SupportAgentResponse)
async def chat(
    request: SupportAgentRequest,
    session: SessionDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> SupportAgentResponse:
    _check_internal_token(x_internal_token)
    return await support_turn(session, request)
