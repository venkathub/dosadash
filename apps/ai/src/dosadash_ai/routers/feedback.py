"""Internal feedback triage endpoint (Phase 13 slice 3).

Thin: token check + hand off to feedback_triage (which owns the LLM call,
the deterministic verdict policy, and the never-5xx fallback)."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai import feedback_triage
from dosadash_ai.config import get_settings
from dosadash_shared import FeedbackTriageRequest, FeedbackTriageResponse

router = APIRouter(prefix="/internal/feedback", tags=["internal:feedback"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/triage", response_model=FeedbackTriageResponse)
async def triage_feedback(
    request: FeedbackTriageRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> FeedbackTriageResponse:
    _check_internal_token(x_internal_token)
    return await feedback_triage.triage(request)
