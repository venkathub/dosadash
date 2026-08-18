"""Admin analytics copilot proxy (Phase 5, docs/04 O16).

POST /api/v1/admin/copilot/ask — admin/owner RBAC. The AI service owns the
LLM, SQL guardrail and read-only execution; this router only adds backoffice
auth (same api→ai pattern as costs/nutrition).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import CopilotAnswer, CopilotAskIn, Role

router = APIRouter(prefix="/api/v1/admin/copilot", tags=["admin:copilot"])

AdminUser = require_role(Role.ADMIN, Role.OWNER)


@router.post("/ask", response_model=CopilotAnswer)
async def copilot_ask(
    request: CopilotAskIn,
    ai: Annotated[AIClient, Depends(get_ai_client)],
    admin: User = AdminUser,
) -> CopilotAnswer:
    try:
        return await ai.copilot_ask(request, admin_user_id=admin.id)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Copilot unavailable") from exc
