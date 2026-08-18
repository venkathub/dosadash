"""Internal copilot endpoint (Phase 5): text-to-SQL analytics.

POST /internal/copilot/ask — X-Internal-Token guarded (api → ai). The api
side adds admin/owner RBAC; this side owns the LLM, guardrail and read-only
execution.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.copilot.agent import ask
from dosadash_shared import CopilotAnswer, CopilotAskIn

router = APIRouter(prefix="/internal/copilot", tags=["internal:copilot"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/ask", response_model=CopilotAnswer)
async def copilot_ask(
    request: CopilotAskIn,
    x_internal_token: Annotated[str, Header()] = "",
    x_admin_user_id: Annotated[str, Header()] = "",
) -> CopilotAnswer:
    _check_internal_token(x_internal_token)
    return await ask(
        request.question,
        session_id=f"copilot-{x_admin_user_id or 'admin'}",
        user_id=x_admin_user_id or None,
    )
