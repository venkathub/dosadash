"""Internal promo suggestion endpoint (Phase 7).

POST /internal/promo/suggest — X-Internal-Token guarded (api → ai). Never
5xxs on LLM failure: the agent degrades to deterministic drafts
(fallback=true) so the owner still gets mined suggestions.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.promo.agent import suggest_promos
from dosadash_shared import PromoSuggestResult

router = APIRouter(prefix="/internal/promo", tags=["internal:promo"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/suggest", response_model=PromoSuggestResult)
async def suggest(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
    x_admin_user_id: Annotated[str, Header()] = "",
) -> PromoSuggestResult:
    _check_internal_token(x_internal_token)
    session_id = f"admin:{x_admin_user_id}" if x_admin_user_id else None
    return await suggest_promos(session, session_id=session_id)
