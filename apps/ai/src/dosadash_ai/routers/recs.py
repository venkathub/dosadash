"""Internal recommendations endpoint (Phase 7).

POST /internal/recs — X-Internal-Token guarded (api → ai). Never 5xxs on
model problems: the serving layer degrades ALS → embedding → popularity
internally; only infrastructure failures surface.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.recsys.serve import recommend
from dosadash_shared import RecsRequest, RecsResponse

router = APIRouter(prefix="/internal/recs", tags=["internal:recs"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("", response_model=RecsResponse)
async def recs(
    request: RecsRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> RecsResponse:
    _check_internal_token(x_internal_token)
    return await recommend(session, request)
