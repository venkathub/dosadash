"""Cost dashboard (Phase 4 LLMOps): LLM spend from Langfuse via the AI service.

GET /api/v1/admin/costs/daily — admin/owner RBAC. The AI service owns the
Langfuse keys and normalizes the metrics; this router only proxies with
backoffice auth (same api→ai pattern as nutrition enrichment).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import User
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import CacheStatsResponse, CostSummaryResponse, Role

router = APIRouter(prefix="/api/v1/admin/costs", tags=["admin:costs"])

AdminUser = require_role(Role.ADMIN, Role.OWNER)


@router.get("/daily", response_model=CostSummaryResponse)
async def daily_costs(
    ai: Annotated[AIClient, Depends(get_ai_client)],
    days: Annotated[int, Query(ge=1, le=60)] = 30,
    admin: User = AdminUser,
) -> CostSummaryResponse:
    try:
        return await ai.daily_costs(days=days)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Cost data unavailable") from exc


@router.get("/cache", response_model=CacheStatsResponse)
async def cache_stats(
    ai: Annotated[AIClient, Depends(get_ai_client)],
    admin: User = AdminUser,
) -> CacheStatsResponse:
    """Semantic-cache hit rate + provider prompt-cache share (Phase 9)."""
    try:
        return await ai.cache_stats()
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Cache stats unavailable") from exc
