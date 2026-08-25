"""Feedback triage runner (Phase 13 slice 3) — one implementation shared by
the worker beat (`feedback.triage_pending`) and the admin triage-now button.

Per report: ai triage (LLM assessment + deterministic verdict) → persist
verdict + provenance → apply GitHub labels best-effort. An unreachable ai
service leaves the report untouched for the next run (never lost); a
GitHub label failure is recorded but does not block the local verdict —
the admin tab remains authoritative even when the mirror lags.

Re-mirror pass (postmortem, prod report #3): intake's GitHub mirror is
best-effort, so a transient GitHub outage leaves a report with no issue.
The whole fixer pipeline is label-driven on the issue — without one, an
admin approval lands locally and then NOTHING happens, silently. Every run
therefore first re-mirrors unmirrored reports and applies the labels their
CURRENT status implies (triage verdict labels, ai:approved/ai:rejected for
already-decided reports), which un-stalls the loop end to end.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport
from dosadash_api.services import feedback_notify, feedback_service
from dosadash_api.services.ai_client import AIClient, AIServiceError
from dosadash_api.services.github_client import GitHubClient, GitHubError
from dosadash_shared import (
    LABEL_AI_APPROVED,
    LABEL_AI_AUTO_FIX,
    LABEL_AI_NEEDS_APPROVAL,
    LABEL_AI_REJECTED,
    FeedbackStatus,
    FeedbackTriageRequest,
    TriageVerdict,
)

logger = logging.getLogger(__name__)

# Verdict → local status projection (labels are the automation signal;
# status is what the admin tab reads without a GitHub round-trip).
_VERDICT_STATUS: dict[TriageVerdict, FeedbackStatus] = {
    TriageVerdict.AUTO_FIX: FeedbackStatus.AUTO_FIX,
    TriageVerdict.NEEDS_APPROVAL: FeedbackStatus.NEEDS_APPROVAL,
    TriageVerdict.DISMISS: FeedbackStatus.DISMISSED,
}

# Local status → the automation labels a late-mirrored issue must carry so
# the pipeline resumes exactly where the report already is. RECEIVED/TRACKED
# carry none (triage will add its own); terminal FIXED/DISMISSED stay bare.
_STATUS_LABELS: dict[FeedbackStatus, list[str]] = {
    FeedbackStatus.AUTO_FIX: [LABEL_AI_AUTO_FIX],
    FeedbackStatus.NEEDS_APPROVAL: [LABEL_AI_NEEDS_APPROVAL],
    FeedbackStatus.APPROVED: [LABEL_AI_APPROVED],  # fixer trigger — resumes the loop
    FeedbackStatus.REJECTED: [LABEL_AI_REJECTED],
}


async def remirror_unmirrored(
    session: AsyncSession, github: GitHubClient, *, limit: int = 10
) -> dict[str, int]:
    """Create issues for reports whose intake mirror failed, then apply the
    labels their current status implies. Commits per report."""
    if not github.enabled:
        return {"remirrored": 0, "remirror_failures": 0}
    rows = (
        (
            await session.execute(
                select(FeedbackReport)
                .where(
                    FeedbackReport.github_issue_number.is_(None),
                    FeedbackReport.status.notin_(
                        [FeedbackStatus.DISMISSED.value, FeedbackStatus.FIXED.value]
                    ),
                )
                .order_by(FeedbackReport.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    remirrored = 0
    failures = 0
    for report in rows:
        try:
            issue = await github.create_issue(
                title=feedback_service.issue_title(report),
                body=feedback_service.build_issue_body(report, env=get_settings().env),
                labels=feedback_service.issue_labels(report),
            )
            report.github_issue_number = issue
            report.github_error = None
            status_labels = _STATUS_LABELS.get(FeedbackStatus(report.status), [])
            if status_labels:
                await github.add_labels(issue, status_labels)
            if report.status == FeedbackStatus.RECEIVED:
                report.status = FeedbackStatus.TRACKED
            await session.commit()
            remirrored += 1
            logger.info("re-mirrored report #%s → issue #%s", report.id, issue)
        except GitHubError as exc:
            failures += 1
            report.github_error = f"re-mirror failed: {exc}"[:300]
            await session.commit()
            logger.warning("re-mirror failed for report #%s: %s", report.id, exc)
    return {"remirrored": remirrored, "remirror_failures": failures}


async def triage_pending(
    session: AsyncSession,
    ai: AIClient,
    github: GitHubClient,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Re-mirror stalled reports, then triage untriaged ones; commits per
    report so a mid-run failure never rolls back completed work."""
    mirror_summary = await remirror_unmirrored(session, github)
    rows = (
        (
            await session.execute(
                select(FeedbackReport)
                .where(
                    FeedbackReport.status.in_(
                        [FeedbackStatus.RECEIVED.value, FeedbackStatus.TRACKED.value]
                    ),
                    FeedbackReport.triage.is_(None),
                )
                .order_by(FeedbackReport.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    triaged = 0
    skipped = 0
    label_failures = 0
    notified = 0
    for report in rows:
        try:
            result = await ai.triage_feedback(
                FeedbackTriageRequest(
                    report_id=report.id,
                    type=report.type,
                    title=report.title,
                    description=report.description,
                    reporter_tier=report.reporter_tier,
                )
            )
        except AIServiceError as exc:
            # ai unreachable — leave the report for the next run.
            logger.warning("triage skipped for report #%s: %s", report.id, exc)
            skipped += 1
            continue

        report.triage = {
            "verdict": result.verdict,
            "assessment": result.assessment.model_dump() if result.assessment else None,
            "labels": result.labels,
            "violations": result.violations,
            "fallback": result.fallback,
            "model": result.model,
            "prompt_version": result.prompt_version,
            "at": datetime.now(UTC).isoformat(),
        }
        report.status = _VERDICT_STATUS[result.verdict]

        if result.labels and report.github_issue_number and github.enabled:
            try:
                await github.add_labels(report.github_issue_number, result.labels)
            except GitHubError as exc:
                # verdict stands locally; the mirror can catch up later.
                label_failures += 1
                report.github_error = f"label apply failed: {exc}"[:300]
                logger.warning("labels failed for report #%s: %s", report.id, exc)

        await session.commit()  # per report — done work survives a crash
        triaged += 1
        if result.verdict == TriageVerdict.NEEDS_APPROVAL:
            # Telegram decision cards (Phase 6 PO pattern) — best-effort,
            # after commit; the admin web tab is always the fallback.
            notified += await feedback_notify.notify_admins_feedback(session, report)
    return {
        "examined": len(rows),
        "triaged": triaged,
        "skipped": skipped,
        "label_failures": label_failures,
        "notified": notified,
        **mirror_summary,
    }
