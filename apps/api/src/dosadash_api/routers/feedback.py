"""Feedback intake — the GUI 🐞 button lands here (Phase 13, docs/14).

Trust model:
- Anonymous reports allowed (browse-time bugs are real signal); the
  feedback rate-limit tier (5/min, user-or-IP identity) + dedupe hash are
  the abuse controls.
- Text is phone-redacted (Hard Rule 8) BEFORE the row is stored — the
  description is mirrored into a GitHub issue, which is outside our
  infrastructure.
- The local row is the source of truth and commits first; the GitHub
  mirror is best-effort (an outage records `github_error` and the report
  stays RECEIVED — never a 5xx for the reporter, hotfix-#72 pattern).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport
from dosadash_api.db.session import get_session
from dosadash_api.routers.chat import OptionalUser
from dosadash_api.services import feedback_events, feedback_service
from dosadash_api.services.github_client import GitHubClient, GitHubError, get_github_client
from dosadash_shared import (
    FeedbackCreateIn,
    FeedbackEventStage,
    FeedbackOut,
    FeedbackStatus,
    ReporterTier,
    Role,
    redact_phones,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
GitHubDep = Annotated[GitHubClient, Depends(get_github_client)]

# A report in one of these states is still "open" — an identical submission
# collapses onto it instead of filing a second GitHub issue.
_OPEN_STATUSES = (
    FeedbackStatus.RECEIVED,
    FeedbackStatus.TRACKED,
    FeedbackStatus.AUTO_FIX,
    FeedbackStatus.NEEDS_APPROVAL,
    FeedbackStatus.APPROVED,
)


def _tier(user_role: Role | None) -> ReporterTier:
    if user_role is None:
        return ReporterTier.ANON
    if user_role in (Role.KITCHEN_STAFF, Role.ADMIN, Role.OWNER):
        return ReporterTier.STAFF
    return ReporterTier.CUSTOMER


@router.post("", response_model=FeedbackOut, status_code=201)
async def create_feedback(
    body: FeedbackCreateIn,
    user: OptionalUser,
    session: SessionDep,
    github: GitHubDep,
    response: Response,
) -> FeedbackOut:
    # Hard Rule 8: redact BEFORE anything is stored or leaves the api.
    title = redact_phones(body.title.strip())
    description = redact_phones(body.description.strip())
    dedupe = feedback_service.compute_dedupe_hash(body.type, title, description)

    existing = (
        await session.execute(
            select(FeedbackReport)
            .where(
                FeedbackReport.dedupe_hash == dedupe,
                FeedbackReport.status.in_([s.value for s in _OPEN_STATUSES]),
            )
            .order_by(FeedbackReport.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        response.status_code = 200
        out = FeedbackOut.model_validate(existing)
        out.duplicate = True
        return out

    context = body.context.model_dump(exclude_none=True) if body.context else None
    report = FeedbackReport(
        user_id=user.id if user else None,
        reporter_tier=_tier(user.role if user else None),
        type=body.type,
        status=FeedbackStatus.RECEIVED,
        title=title,
        description=description,
        context=context,
        dedupe_hash=dedupe,
    )
    session.add(report)
    await session.flush()  # need report.id for the issue body

    # Phase 14: the timeline starts at birth — RECEIVED always, TRACKED
    # when the mirror lands (same commit; publish after).
    stages = [FeedbackEventStage.RECEIVED]
    feedback_events.record(
        session,
        report,
        FeedbackEventStage.RECEIVED,
        actor="system",
        payload={"tier": report.reporter_tier},
    )

    if github.enabled:
        try:
            report.github_issue_number = await github.create_issue(
                title=feedback_service.issue_title(report),
                body=feedback_service.build_issue_body(report, env=get_settings().env),
                labels=feedback_service.issue_labels(report),
            )
            report.status = FeedbackStatus.TRACKED
            feedback_events.record(
                session,
                report,
                FeedbackEventStage.TRACKED,
                actor="system",
                payload={"issue": report.github_issue_number},
            )
            stages.append(FeedbackEventStage.TRACKED)
        except GitHubError as exc:
            # Store-only degrade: the admin tab shows the row + error, a
            # later slice can re-mirror. Never fail the reporter for GitHub.
            report.github_error = str(exc)[:300]
            logger.warning("feedback #%s stored; GitHub mirror failed: %s", report.id, exc)
    else:
        report.github_error = "github integration disabled (API_GITHUB_TOKEN/REPO unset)"

    await session.commit()
    await session.refresh(report)
    for stage in stages:
        await feedback_events.publish(report.id, stage)
    return FeedbackOut.model_validate(report)
