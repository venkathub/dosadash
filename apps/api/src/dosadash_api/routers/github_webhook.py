"""GitHub → DosaDash webhook (Phase 14 slice 1): the self-healing loop's
tail — fixer dispatch, RCA, PRs, merges, verification, reopens — happens on
GitHub; this endpoint syncs it back into `feedback_events` in real time so
the portal, Telegram feed, and metrics never need a GitHub round-trip.

Auth mirrors the aggregator webhook (Razorpay pattern): HMAC-SHA256 of the
raw body against a shared secret, here in GitHub's own header
`X-Hub-Signature-256` ("sha256=<hex>"). 503 when unconfigured, 403 on a bad
signature. Extra guards:
- repo pinning: events from any repo other than the configured one are
  acknowledged-and-ignored (a webhook misconfigured on a fork can't write).
- idempotency: `X-GitHub-Delivery` GUIDs are recorded per event; GitHub
  redeliveries no-op (200 + ignored).
- self-echo damping: labels our own api applies (triage verdicts,
  decisions) don't re-record — except the fixer TRIGGER labels, where the
  label landing IS the dispatch signal (stage FIX_STARTED), and
  ai:needs-approval when it means the fixer ESCALATED mid-run.

Status stays a projection: feedback_events.record() only moves it when the
transition is legal, so out-of-order deliveries degrade to timeline-only.
"""

import hashlib
import hmac
import json
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport
from dosadash_api.db.session import get_session
from dosadash_api.services import feedback_events, feedback_notify
from dosadash_shared import (
    FIX_BRANCH_PREFIX,
    FIXER_TRIGGER_LABELS,
    LABEL_AI_FIXED,
    LABEL_AI_NEEDS_APPROVAL,
    LABEL_AI_VERIFIED,
    RCA_COMMENT_MARKER,
    SPEC_COMMENT_MARKER,
    VERIFICATION_COMMENT_MARKER,
    FeedbackEventStage,
    FeedbackStatus,
    redact_phones,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["github-webhook"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_ACTOR = "webhook:github"
_EXCERPT_CHARS = 500

# Statuses in which an incoming ai:needs-approval label means "the fixer
# escalated mid-run" (vs. the api's own triage echo, which is ignored).
_ESCALATABLE = {
    FeedbackStatus.AUTO_FIX,
    FeedbackStatus.APPROVED,
    FeedbackStatus.FIXING,
    FeedbackStatus.PR_OPEN,
}

_FIXES_RE = re.compile(r"(?:Fixes|Closes)\s+#(\d+)", re.IGNORECASE)


def _secret() -> bytes:
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GitHub webhook not configured")
    return secret.encode()


def _check_signature(payload: bytes, provided: str) -> None:
    expected = "sha256=" + hmac.new(_secret(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Bad webhook signature")


def _excerpt(text: str) -> str:
    # Rule 8 defensively: our automation's comments should never carry
    # phones, but excerpts land in a DB column the portal renders.
    return redact_phones(text.strip())[:_EXCERPT_CHARS]


async def _report_by_issue(session: AsyncSession, issue_number: int) -> FeedbackReport | None:
    return await session.scalar(
        select(FeedbackReport).where(FeedbackReport.github_issue_number == issue_number)
    )


def _pr_issue_number(pr: dict[str, Any]) -> int | None:
    """Map a PR back to its issue via the fixer's branch contract
    (`fix/issue-N`), falling back to a `Fixes #N` body scan."""
    head_ref = (pr.get("head") or {}).get("ref") or ""
    if head_ref.startswith(FIX_BRANCH_PREFIX):
        suffix = head_ref.removeprefix(FIX_BRANCH_PREFIX)
        if suffix.isdigit():
            return int(suffix)
    match = _FIXES_RE.search(pr.get("body") or "")
    return int(match.group(1)) if match else None


Stage = FeedbackEventStage


def _issue_label_stage(label: str, current: FeedbackStatus) -> Stage | None:
    """Which stage (if any) a freshly-applied label maps to."""
    if label in FIXER_TRIGGER_LABELS:
        return Stage.FIX_STARTED
    if label == LABEL_AI_FIXED:
        return Stage.FIXED
    if label == LABEL_AI_VERIFIED:
        return Stage.VERIFIED
    if label == LABEL_AI_NEEDS_APPROVAL and current in _ESCALATABLE:
        return Stage.ESCALATED
    return None  # self-echo (triage/decision labels) or unregistered label


def _handle_issues(
    report: FeedbackReport, payload: dict[str, Any]
) -> tuple[Stage, dict[str, Any]] | None:
    action = payload.get("action")
    if action == "labeled":
        label = (payload.get("label") or {}).get("name") or ""
        stage = _issue_label_stage(label, FeedbackStatus(report.status))
        if stage is None:
            return None
        return stage, {"action": action, "label": label}
    if action == "reopened":
        return Stage.REOPENED, {"action": action}
    if action == "closed":
        issue = payload.get("issue") or {}
        return Stage.CLOSED, {"action": action, "state_reason": issue.get("state_reason")}
    return None


def _handle_issue_comment(payload: dict[str, Any]) -> tuple[Stage, dict[str, Any]] | None:
    if payload.get("action") != "created":
        return None
    body = (payload.get("comment") or {}).get("body") or ""
    if body.startswith(RCA_COMMENT_MARKER):
        return Stage.RCA_POSTED, {"excerpt": _excerpt(body)}
    if body.startswith(VERIFICATION_COMMENT_MARKER):
        return Stage.VERIFICATION_POSTED, {"excerpt": _excerpt(body)}
    if body.startswith(SPEC_COMMENT_MARKER):
        # Phase 15 S2: the spec agent's scope draft — timeline-only (the
        # report stays NEEDS_APPROVAL; the human decides WITH the spec).
        return Stage.SPEC_POSTED, {"excerpt": _excerpt(body)}
    return None


def _handle_pull_request(payload: dict[str, Any]) -> tuple[Stage, dict[str, Any]] | None:
    pr = payload.get("pull_request") or {}
    action = payload.get("action")
    detail = {"pr_number": pr.get("number"), "html_url": pr.get("html_url")}
    if action == "opened":
        return Stage.PR_OPENED, detail
    if action == "closed":
        if pr.get("merged"):
            return Stage.PR_MERGED, detail
        return Stage.PR_CLOSED, detail
    return None


@router.post("/api/v1/github/webhook")
async def github_webhook(
    request: Request,
    session: SessionDep,
    x_hub_signature_256: Annotated[str, Header()] = "",
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
) -> dict[str, Any]:
    body = await request.body()
    _check_signature(body, x_hub_signature_256)

    if x_github_event == "ping":
        return {"ok": True, "event": "ping"}

    try:
        payload: dict[str, Any] = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON body") from exc

    repo = (payload.get("repository") or {}).get("full_name") or ""
    if repo != get_settings().github_repo:
        return {"ok": True, "ignored": "repo"}

    if await feedback_events.seen_delivery(session, x_github_delivery):
        return {"ok": True, "ignored": "duplicate-delivery"}

    # Resolve the affected report + the stage this event maps to.
    report: FeedbackReport | None = None
    result: tuple[Stage, dict[str, Any]] | None = None

    if x_github_event in ("issues", "issue_comment"):
        issue_number = (payload.get("issue") or {}).get("number")
        if issue_number is not None:
            report = await _report_by_issue(session, int(issue_number))
        if report is not None:
            if x_github_event == "issues":
                result = _handle_issues(report, payload)
            else:
                result = _handle_issue_comment(payload)
    elif x_github_event == "pull_request":
        pr = payload.get("pull_request") or {}
        issue_number = _pr_issue_number(pr)
        if issue_number is not None:
            report = await _report_by_issue(session, issue_number)
        if report is not None:
            result = _handle_pull_request(payload)
            if result is not None and pr.get("number"):
                report.fix_pr_number = int(pr["number"])
    else:
        return {"ok": True, "ignored": f"event:{x_github_event}"}

    if report is None or result is None:
        return {"ok": True, "ignored": "unmapped"}

    stage, detail = result
    feedback_events.record(
        session,
        report,
        stage,
        actor=_ACTOR,
        payload=detail,
        delivery_id=x_github_delivery or None,
    )
    await session.commit()
    await feedback_events.publish(report.id, stage, detail=detail)
    # Phase 14 slice 2: Telegram anchor-card update (best-effort; ping
    # stages — escalation/verified/reopened — also reply audibly).
    await feedback_notify.notify_stage(session, report, stage)
    logger.info("github webhook: report #%s stage %s", report.id, stage.value)
    return {"ok": True, "report_id": report.id, "stage": stage.value}
