"""Feedback triage runner (Phase 13 slice 3) — one implementation shared by
the worker beat (`feedback.triage_pending`) and the admin triage-now button.

Per report: ai triage (LLM assessment + deterministic verdict) → persist
verdict + provenance → apply GitHub labels best-effort. An unreachable ai
service leaves the report untouched for the next run (never lost); a
GitHub label failure is recorded but does not block the local verdict —
the admin tab remains authoritative even when the mirror lags.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport
from dosadash_api.services import feedback_notify
from dosadash_api.services.ai_client import AIClient, AIServiceError
from dosadash_api.services.github_client import GitHubClient, GitHubError
from dosadash_shared import (
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


async def triage_pending(
    session: AsyncSession,
    ai: AIClient,
    github: GitHubClient,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Triage untriaged reports; commits per report so a mid-run failure
    never rolls back completed verdicts. Returns a run summary."""
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
    }
