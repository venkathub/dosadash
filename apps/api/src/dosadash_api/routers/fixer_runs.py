"""Fixer/verifier run ingest (Phase 14 slice 3) — the eval_runs CI-ingest
pattern applied to the self-healing loop's workflows.

Both workflows finish with a best-effort `curl` step that POSTs their own
outcome here. This carries run-level truth the GitHub webhooks cannot: a
fixer run that died WITHOUT opening a PR produces no label/PR event at all
— here it lands as conclusion='failure', raises a FIX_FAILED timeline
event, and pings the linked admins (a dead run needs a human eye).

Auth: X-Internal-Token (same shared secret as the eval ingest — the repo
secret INTERNAL_API_TOKEN already exists). Idempotency: (workflow, run_id,
run_attempt) unique → replays return 200 duplicate:true (aggregator
pattern)."""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport, FixerRun
from dosadash_api.db.session import get_session
from dosadash_api.services import feedback_events, feedback_notify
from dosadash_shared import FeedbackEventStage, FeedbackStatus, FixerRunIn, FixerRunOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal:fixer-runs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# A failed fix run only raises FIX_FAILED while the report is actually
# mid-fix — a late replay after escalation/merge must not re-alarm.
_FAILABLE = {
    FeedbackStatus.AUTO_FIX,
    FeedbackStatus.APPROVED,
    FeedbackStatus.FIXING,
    FeedbackStatus.PR_OPEN,
}


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/api/v1/internal/fixer-runs", response_model=FixerRunOut, status_code=201)
async def ingest_fixer_run(
    body: FixerRunIn,
    session: SessionDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> FixerRunOut:
    _check_internal_token(x_internal_token)

    existing = await session.scalar(
        select(FixerRun).where(
            FixerRun.workflow == body.workflow,
            FixerRun.run_id == body.run_id,
            FixerRun.run_attempt == body.run_attempt,
        )
    )
    if existing is not None:
        out = FixerRunOut.model_validate(existing)
        out.duplicate = True
        return out

    report: FeedbackReport | None = None
    if body.issue_number is not None:
        report = await session.scalar(
            select(FeedbackReport).where(FeedbackReport.github_issue_number == body.issue_number)
        )

    run = FixerRun(
        report_id=report.id if report else None,
        workflow=body.workflow,
        run_id=body.run_id,
        run_attempt=body.run_attempt,
        issue_number=body.issue_number,
        conclusion=body.conclusion,
        trigger_label=body.trigger_label,
        model=body.model,
        cost_usd=body.cost_usd,
        input_tokens=body.input_tokens,
        cache_read_tokens=body.cache_read_tokens,
        cache_creation_tokens=body.cache_creation_tokens,
        output_tokens=body.output_tokens,
    )
    session.add(run)

    failed_fix = (
        body.workflow == "fix"
        and body.conclusion != "success"
        and report is not None
        and FeedbackStatus(report.status) in _FAILABLE
    )
    if failed_fix:
        feedback_events.record(
            session,
            report,
            FeedbackEventStage.FIX_FAILED,
            actor="workflow:fix",
            payload={"run_id": body.run_id, "conclusion": body.conclusion},
        )

    await session.commit()
    if failed_fix and report is not None:
        await feedback_events.publish(
            report.id, FeedbackEventStage.FIX_FAILED, detail={"run_id": body.run_id}
        )
        await feedback_notify.notify_stage(session, report, FeedbackEventStage.FIX_FAILED)
        logger.warning(
            "fixer run %s failed for report #%s (issue #%s)",
            body.run_id,
            report.id,
            body.issue_number,
        )

    await session.refresh(run)
    return FixerRunOut.model_validate(run)
