"""Internal inventory endpoint (Phase 6 — apps/ai reasons, apps/api owns
mutations).

POST /internal/inventory/draft-po — X-Internal-Token guarded. Called by the
api worker's nightly task (and the admin "draft now" button). Returns
validated, supplier-grouped draft POs; persisting them as PENDING_APPROVAL
purchase orders is the api's job.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_session
from dosadash_ai.inventory.agent import draft_pos
from dosadash_shared import InventoryDraftRequest, InventoryDraftResult

router = APIRouter(prefix="/internal/inventory", tags=["internal:inventory"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/draft-po", response_model=InventoryDraftResult)
async def draft_purchase_orders(
    request: InventoryDraftRequest,
    session: SessionDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> InventoryDraftResult:
    _check_internal_token(x_internal_token)
    return await draft_pos(
        session, coverage_days=request.coverage_days, session_id=request.session_id
    )
