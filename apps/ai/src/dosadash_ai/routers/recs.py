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
from dosadash_ai.recsys.serve import recommend, suggest_checkout
from dosadash_shared import CheckoutSuggestResponse, RecsRequest, RecsResponse

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


@router.post("/checkout", response_model=CheckoutSuggestResponse)
async def checkout_suggestions(
    request: RecsRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_internal_token: Annotated[str, Header()] = "",
) -> CheckoutSuggestResponse:
    """Combo-completion + pairing-gap add-ons for the checkout footer (the
    rule engine measured by the synthetic A/B sim, ranked per-customer)."""
    _check_internal_token(x_internal_token)
    return await suggest_checkout(session, request)
