"""Feedback lifecycle event recorder (Phase 14 slice 1).

One writer for the append-only `feedback_events` timeline, shared by every
stage source: the local pipeline (intake, triage, decisions), the GitHub
webhook, and the reconciler. Each recorded stage optionally PROJECTS onto
`feedback_reports.status` — but only when the report's current status makes
that transition legal, so out-of-order webhook deliveries and redeliveries
degrade to event-only records instead of corrupting the projection.

Design notes:
- `record()` only stages ORM changes; the caller owns the commit (same
  contract as audit.record). Publish AFTER commit via `publish()` so the
  portal WS never sees a state that later rolled back.
- GitHub labels remain the authoritative automation signal (docs/14);
  status stays the local projection — this module is what finally keeps
  that projection honest past APPROVED.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events as bus
from dosadash_api.db.models import FeedbackEvent, FeedbackReport
from dosadash_shared import FeedbackEventStage, FeedbackStatus

logger = logging.getLogger(__name__)

Stage = FeedbackEventStage
Status = FeedbackStatus

# stage → (projected status, statuses the report must currently be in).
# A stage missing here is timeline-only (never moves the projection).
# The loop is deliberately re-entrant: REOPENED reports may flow through
# fix/PR/merge stages again.
_ACTIVE_FIX_STATUSES = frozenset(
    {Status.AUTO_FIX, Status.APPROVED, Status.FIXING, Status.PR_OPEN, Status.REOPENED}
)

STAGE_PROJECTION: dict[Stage, tuple[Status, frozenset[Status]]] = {
    Stage.TRACKED: (Status.TRACKED, frozenset({Status.RECEIVED})),
    Stage.APPROVED: (Status.APPROVED, frozenset({Status.NEEDS_APPROVAL})),
    Stage.REJECTED: (Status.REJECTED, frozenset({Status.NEEDS_APPROVAL})),
    Stage.FIX_STARTED: (
        Status.FIXING,
        frozenset({Status.AUTO_FIX, Status.APPROVED, Status.REOPENED}),
    ),
    Stage.ESCALATED: (
        Status.NEEDS_APPROVAL,
        frozenset({Status.AUTO_FIX, Status.APPROVED, Status.FIXING, Status.PR_OPEN}),
    ),
    Stage.PR_OPENED: (
        Status.PR_OPEN,
        frozenset({Status.AUTO_FIX, Status.APPROVED, Status.FIXING, Status.REOPENED}),
    ),
    Stage.PR_MERGED: (Status.FIXED, _ACTIVE_FIX_STATUSES | {Status.TRACKED}),
    Stage.FIXED: (Status.FIXED, _ACTIVE_FIX_STATUSES | {Status.TRACKED}),
    Stage.VERIFIED: (
        Status.VERIFIED,
        frozenset({Status.FIXED, Status.PR_OPEN, Status.FIXING, Status.REOPENED}),
    ),
    Stage.REOPENED: (
        Status.REOPENED,
        frozenset({Status.FIXED, Status.VERIFIED, Status.PR_OPEN, Status.FIXING}),
    ),
    Stage.DISMISSED: (Status.DISMISSED, frozenset({Status.RECEIVED, Status.TRACKED})),
}


def record(
    session: AsyncSession,
    report: FeedbackReport,
    stage: Stage,
    *,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
    delivery_id: str | None = None,
) -> FeedbackEvent:
    """Append one timeline event and (when legal) project it onto the
    report's status. Caller commits, then calls `publish()`."""
    projection = STAGE_PROJECTION.get(stage)
    if projection is not None:
        target, allowed_from = projection
        current = FeedbackStatus(report.status)
        if current in allowed_from:
            report.status = target
            if stage == Stage.VERIFIED:
                report.verified_at = datetime.now(UTC).replace(tzinfo=None)
    event = FeedbackEvent(
        report_id=report.id,
        stage=stage.value,
        actor=actor,
        payload=payload,
        delivery_id=delivery_id,
    )
    session.add(event)
    return event


async def seen_delivery(session: AsyncSession, delivery_id: str | None) -> bool:
    """Webhook idempotency: has this GitHub delivery GUID been recorded?"""
    if not delivery_id:
        return False
    existing = await session.scalar(
        select(FeedbackEvent.id).where(FeedbackEvent.delivery_id == delivery_id).limit(1)
    )
    return existing is not None


async def publish(report_id: int, stage: Stage, *, detail: dict[str, Any] | None = None) -> None:
    """Best-effort pubsub:feedback fan-out — call AFTER commit."""
    await bus.publish_feedback_event(stage.value, report_id=report_id, detail=detail)
