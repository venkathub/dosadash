"""GitHub reconciler (Phase 14 slice 1) — the webhook's safety net.

The webhook gives the loop real-time sync; this beat pass gives it TRUTH.
Every 15 minutes it diffs each in-flight report's local status projection
against the issue's CURRENT labels/state on GitHub (plus the fixer's PR,
found via the `fix/issue-N` branch contract) and corrects drift with a
SYNCED timeline event. Missed webhook deliveries, a webhook that was never
configured, or label flips made by humans in the GitHub UI all heal here —
worst case the portal is 15 minutes stale, never wrong forever.

Contracts:
- labels are authoritative (docs/14): precedence order lives in
  dosadash_shared.LABEL_STATUS_PRECEDENCE (coherence eval-gated).
- an open PR upgrades APPROVED/AUTO_FIX/FIXING → PR_OPEN; a merged PR
  outranks label-derived APPROVED-era statuses (the merge happened even if
  ai:fixed never landed).
- an issue closed as not_planned while still pre-fix → DISMISSED.
- GitHub unreachable for one report → skip it, touch the rest (per-report
  commits, triage-runner pattern).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport
from dosadash_api.services import feedback_events
from dosadash_api.services.github_client import GitHubClient, GitHubError
from dosadash_shared import LABEL_STATUS_PRECEDENCE, FeedbackEventStage, FeedbackStatus

logger = logging.getLogger(__name__)

Status = FeedbackStatus

# Statuses the reconciler re-checks. REJECTED/DISMISSED/VERIFIED are
# terminal; RECEIVED has no issue yet (the re-mirror pass owns it).
_SYNCABLE_STATUSES = (
    Status.TRACKED,
    Status.AUTO_FIX,
    Status.NEEDS_APPROVAL,
    Status.APPROVED,
    Status.FIXING,
    Status.PR_OPEN,
    Status.FIXED,
    Status.REOPENED,
)

# Statuses that a discovered open PR upgrades to PR_OPEN.
_PR_UPGRADEABLE = {Status.AUTO_FIX, Status.APPROVED, Status.FIXING}

# Statuses where a not_planned close means the report was dismissed upstream.
_DISMISSABLE = {Status.TRACKED, Status.AUTO_FIX, Status.NEEDS_APPROVAL}


def derive_status(
    current: Status,
    *,
    labels: list[str],
    issue_state: str | None,
    state_reason: str | None,
    pr: dict[str, Any] | None,
) -> Status:
    """Pure derivation of the correct local status from GitHub truth.

    Precedence: verified label > reopen-after-fix > merged PR / fixed label
    > open PR > decision/triage labels > close-without-action > keep."""
    label_status = next(
        (status for label, status in LABEL_STATUS_PRECEDENCE if label in labels), None
    )
    if label_status == Status.VERIFIED:
        return Status.VERIFIED
    if issue_state == "open" and current in (Status.FIXED, Status.VERIFIED):
        # merged-then-reopened (verifier or human): the reopen outranks any
        # stale ai:fixed label or merged PR — the fix demonstrably failed.
        return Status.REOPENED
    if pr and pr.get("merged_at"):
        return Status.FIXED
    if label_status == Status.FIXED:
        return Status.FIXED
    if pr and pr.get("state") == "open" and current in _PR_UPGRADEABLE:
        return Status.PR_OPEN
    if issue_state == "closed" and state_reason == "not_planned" and current in _DISMISSABLE:
        return Status.DISMISSED
    if label_status is not None and current in (Status.TRACKED,):
        # a label landed but the local verdict write was lost — adopt it.
        return label_status
    return current


async def sync_github(
    session: AsyncSession, github: GitHubClient, *, limit: int = 30
) -> dict[str, Any]:
    """Reconcile in-flight reports against GitHub; commits per report."""
    if not github.enabled:
        return {"examined": 0, "corrected": 0, "skipped": 0, "disabled": True}
    rows = (
        (
            await session.execute(
                select(FeedbackReport)
                .where(
                    FeedbackReport.github_issue_number.is_not(None),
                    FeedbackReport.status.in_([s.value for s in _SYNCABLE_STATUSES]),
                )
                .order_by(FeedbackReport.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    examined = 0
    corrected = 0
    skipped = 0
    for report in rows:
        examined += 1
        try:
            issue = await github.get_issue(report.github_issue_number)
            pr = await github.find_fix_pr(report.github_issue_number)
        except GitHubError as exc:
            skipped += 1
            logger.warning("sync skipped for report #%s: %s", report.id, exc)
            continue

        if pr is not None and report.fix_pr_number != pr["number"]:
            report.fix_pr_number = pr["number"]

        current = Status(report.status)
        target = derive_status(
            current,
            labels=issue["labels"],
            issue_state=issue["state"],
            state_reason=issue["state_reason"],
            pr=pr,
        )
        if target == current:
            # fix_pr_number backfill alone still deserves a commit.
            if session.dirty:
                await session.commit()
            continue

        detail = {
            "from": current.value,
            "to": target.value,
            "labels": issue["labels"],
            "issue_state": issue["state"],
            "pr_number": pr["number"] if pr else None,
        }
        event = feedback_events.record(
            session, report, FeedbackEventStage.SYNCED, actor="reconciler", payload=detail
        )
        # SYNCED is timeline-only in the projection map — the reconciler
        # derives the target itself and applies it directly (its whole job
        # is authoritative correction, including transitions the guarded
        # projection would refuse).
        report.status = target
        if target == Status.VERIFIED and report.verified_at is None:
            report.verified_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        corrected += 1
        await feedback_events.publish(report.id, FeedbackEventStage.SYNCED, detail=detail)
        logger.info(
            "reconciled report #%s: %s → %s (event %s)",
            report.id,
            current.value,
            target.value,
            event.id,
        )
    return {"examined": examined, "corrected": corrected, "skipped": skipped}
