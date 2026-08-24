"""Admin feedback inbox (Phase 13 slice 2): the backoffice view of the
self-healing loop. Read-only in this slice — triage/approval mutations
arrive with slices 3–4 and are audited there."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport, User
from dosadash_api.db.session import get_session
from dosadash_shared import (
    AdminFeedbackListOut,
    AdminFeedbackOut,
    FeedbackStatus,
    FeedbackType,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/feedback", tags=["admin:feedback"])

AdminUser = require_role(Role.ADMIN, Role.OWNER)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=AdminFeedbackListOut)
async def list_feedback(
    session: SessionDep,
    status: Annotated[FeedbackStatus | None, Query()] = None,
    type: Annotated[FeedbackType | None, Query()] = None,  # noqa: A002 — wire name
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin: User = AdminUser,
) -> AdminFeedbackListOut:
    query = select(FeedbackReport)
    count_query = select(func.count(FeedbackReport.id))
    if status is not None:
        query = query.where(FeedbackReport.status == status.value)
        count_query = count_query.where(FeedbackReport.status == status.value)
    if type is not None:
        query = query.where(FeedbackReport.type == type.value)
        count_query = count_query.where(FeedbackReport.type == type.value)
    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(FeedbackReport.id.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return AdminFeedbackListOut(
        reports=[AdminFeedbackOut.model_validate(r) for r in rows],
        total=total,
        github_repo=get_settings().github_repo,
    )
