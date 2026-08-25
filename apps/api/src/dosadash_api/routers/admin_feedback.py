"""Admin feedback inbox (Phase 13): the backoffice view of the
self-healing loop, an on-demand triage trigger, and the approval flow
(Telegram cards land on /api/v1/internal/feedback/decision; the tab's
Approve/Reject buttons are the web fallback — one shared transition)."""

import secrets
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackEvent, FeedbackReport, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit, feedback_events, feedback_triage_runner
from dosadash_api.services.ai_client import AIClient, get_ai_client
from dosadash_api.services.github_client import GitHubClient, GitHubError, get_github_client
from dosadash_shared import (
    LABEL_AI_APPROVED,
    LABEL_AI_NEEDS_APPROVAL,
    LABEL_AI_REJECTED,
    AdminFeedbackListOut,
    AdminFeedbackOut,
    FeedbackEventListOut,
    FeedbackEventOut,
    FeedbackEventStage,
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


@router.get("/{report_id}/events", response_model=FeedbackEventListOut)
async def list_feedback_events(
    report_id: int,
    session: SessionDep,
    admin: User = AdminUser,
) -> FeedbackEventListOut:
    """Phase 14: the report's lifecycle timeline (intake → triage →
    decision → fixer → PR → merge → verify), oldest first — the portal's
    drill-down view."""
    report = await session.get(FeedbackReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    rows = (
        (
            await session.execute(
                select(FeedbackEvent)
                .where(FeedbackEvent.report_id == report_id)
                .order_by(FeedbackEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return FeedbackEventListOut(events=[FeedbackEventOut.model_validate(r) for r in rows])


@router.post("/triage-now")
async def triage_now(
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
    github: Annotated[GitHubClient, Depends(get_github_client)],
    admin: User = AdminUser,
) -> dict[str, Any]:
    """Run the triage runner on demand (same code path as the beat).
    ai-unreachable reports are skipped, never lost — re-run later."""
    summary = await feedback_triage_runner.triage_pending(session, ai, github)
    audit.record(
        session, actor=admin, action="feedback.triage_now", entity="feedback", detail=summary
    )
    await session.commit()
    return summary


# ---------------------------------------------------------------- decisions
# One transition for both surfaces: Telegram cards (internal, RBAC
# re-checked via the tg_user_id → User mapping) and the admin tab (JWT).
# Approving flips the GitHub label to ai:approved — that label IS the
# fixer trigger, so this is the human gate of the self-healing loop.

_DECISION_STATUS: dict[str, FeedbackStatus] = {
    "approve": FeedbackStatus.APPROVED,
    "reject": FeedbackStatus.REJECTED,
}
_DECISION_LABEL = {"approve": LABEL_AI_APPROVED, "reject": LABEL_AI_REJECTED}


async def _decide(
    session: AsyncSession,
    github: GitHubClient,
    report: FeedbackReport,
    action: Literal["approve", "reject"],
    actor: User,
) -> None:
    """Apply one decision; caller owns error mapping. Raises ValueError on
    an illegal state (only NEEDS_APPROVAL reports are decidable)."""
    if report.status != FeedbackStatus.NEEDS_APPROVAL:
        raise ValueError(f"report is {report.status}, not NEEDS_APPROVAL")
    report.status = _DECISION_STATUS[action]
    if report.github_issue_number and github.enabled:
        try:
            await github.add_labels(report.github_issue_number, [_DECISION_LABEL[action]])
            await github.remove_label(report.github_issue_number, LABEL_AI_NEEDS_APPROVAL)
            await github.comment(
                report.github_issue_number,
                f"Decision: **{action}d** by {actor.role} (user {actor.id}) via DosaDash.",
            )
        except GitHubError as exc:
            # local decision stands; the mirror lag is recorded for retry.
            report.github_error = f"decision mirror failed: {exc}"[:300]
    audit.record(
        session,
        actor=actor,
        action=f"feedback.{action}",
        entity=f"feedback_report:{report.id}",
        detail={"status": report.status.value},
    )
    # Phase 14: decision lands on the timeline too (record BEFORE commit so
    # event + status share the transaction; publish after, caller-side).
    stage = FeedbackEventStage.APPROVED if action == "approve" else FeedbackEventStage.REJECTED
    feedback_events.record(
        session, report, stage, actor=f"admin:{actor.id}", payload={"role": actor.role.value}
    )
    await session.commit()
    await feedback_events.publish(report.id, stage)


class FeedbackDecisionIn(BaseModel):
    tg_user_id: int
    report_id: int
    action: Literal["approve", "reject"]


class FeedbackDecisionOut(BaseModel):
    ok: bool
    status: str | None = None
    detail: str | None = None


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


internal_router = APIRouter(tags=["internal:feedback"])


@internal_router.post("/api/v1/internal/feedback/decision", response_model=FeedbackDecisionOut)
async def feedback_decision(
    body: FeedbackDecisionIn,
    session: SessionDep,
    github: Annotated[GitHubClient, Depends(get_github_client)],
    x_internal_token: Annotated[str, Header()] = "",
) -> FeedbackDecisionOut:
    """Telegram card decision (bot → api). RBAC re-checked here: the tapping
    Telegram account must map to a linked ADMIN/OWNER user. Soft-fails
    (ok=False) render as card text; hard failures are auth-boundary only."""
    _check_internal_token(x_internal_token)
    user = await session.scalar(select(User).where(User.tg_user_id == body.tg_user_id))
    if user is None or user.role not in (Role.ADMIN, Role.OWNER):
        return FeedbackDecisionOut(ok=False, detail="This Telegram account cannot decide reports.")
    report = await session.get(FeedbackReport, body.report_id)
    if report is None:
        return FeedbackDecisionOut(ok=False, detail="Report not found.")
    try:
        await _decide(session, github, report, body.action, user)
    except ValueError as exc:
        return FeedbackDecisionOut(ok=False, detail=str(exc))
    return FeedbackDecisionOut(ok=True, status=report.status.value)


@router.post("/{report_id}/decision", response_model=FeedbackDecisionOut)
async def feedback_decision_web(
    report_id: int,
    body: dict,
    session: SessionDep,
    github: Annotated[GitHubClient, Depends(get_github_client)],
    admin: User = AdminUser,
) -> FeedbackDecisionOut:
    """Web fallback for the same decision (admin tab buttons)."""
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="action must be approve|reject")
    report = await session.get(FeedbackReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        await _decide(session, github, report, action, admin)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FeedbackDecisionOut(ok=True, status=report.status.value)
