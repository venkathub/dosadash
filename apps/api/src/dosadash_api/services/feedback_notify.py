"""Telegram admin notifications for the self-healing loop.

Phase 13 (slice 4): decision cards for NEEDS_APPROVAL reports — mirror of
po_notify.py. The api resolves recipients (linked admin/owner accounts),
builds the card payload, and calls the bot once per recipient —
best-effort, a bot outage never fails triage. Unconfigured bot/token or
zero linked admins → 0 sends (the admin web tab remains the fallback).

Phase 14 (slice 2): full-lifecycle feed. Every recorded stage updates ONE
anchor status card per (report, admin) — edited in place, so Telegram
stays silent while the timeline stays complete — and actionable/terminal
stages (escalation, verified, reopened) additionally send a ping reply.
Anchor message ids live in `feedback_notifications`. The decision card
flow above stays untouched: it is the actionable surface, the anchor is
the status surface."""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackEvent, FeedbackNotification, FeedbackReport, User
from dosadash_shared import FeedbackEventStage, Role

logger = logging.getLogger(__name__)

Stage = FeedbackEventStage

# Stages that warrant an audible ping (a reply to the anchor card).
# NEEDS_APPROVAL is deliberately absent — it has its own decision card.
LIFECYCLE_PING_STAGES = frozenset(
    {Stage.ESCALATED, Stage.VERIFIED, Stage.REOPENED, Stage.FIX_FAILED}
)

# The anchor renders at most this many timeline rows (oldest dropped).
_TIMELINE_LIMIT = 12


async def _recipients(session: AsyncSession) -> list[int]:
    rows = await session.execute(
        select(User.tg_user_id).where(
            User.role.in_([Role.ADMIN, Role.OWNER]), User.tg_user_id.is_not(None)
        )
    )
    return [tg_id for (tg_id,) in rows.all()]


def _summary(report: FeedbackReport) -> dict:
    triage = report.triage or {}
    assessment = triage.get("assessment") or {}
    settings = get_settings()
    github_url = (
        f"https://github.com/{settings.github_repo}/issues/{report.github_issue_number}"
        if settings.github_repo and report.github_issue_number
        else None
    )
    return {
        "report_id": report.id,
        "type": report.type,
        "title": report.title,
        "summary": assessment.get("summary"),
        "effort": assessment.get("effort"),
        "risk": assessment.get("risk"),
        "github_url": github_url,
    }


async def notify_admins_feedback(session: AsyncSession, report: FeedbackReport) -> int:
    """Send the decision card to every linked admin/owner; returns sends."""
    settings = get_settings()
    if not settings.bot_base_url or not settings.internal_api_token:
        return 0
    recipients = await _recipients(session)
    if not recipients:
        return 0  # nobody linked — admin web tab is the fallback
    payload = _summary(report)
    sent = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for tg_user_id in recipients:
            try:
                resp = await client.post(
                    f"{settings.bot_base_url.rstrip('/')}/internal/feedback-notify",
                    json={"tg_user_id": tg_user_id, **payload},
                    headers={"X-Internal-Token": settings.internal_api_token},
                )
                resp.raise_for_status()
                sent += 1
            except httpx.HTTPError:
                logger.warning("feedback notify failed (report #%s, tg %s)", report.id, tg_user_id)
    return sent


# ------------------------------------------------------ lifecycle (Phase 14)


def _timeline_note(stage: str, payload: dict | None) -> str | None:
    """Tiny per-stage annotation for the card (data extraction only —
    presentation lives bot-side, Hard Rule 10)."""
    if not payload:
        return None
    if stage == Stage.TRIAGED:
        return payload.get("verdict")
    if stage in (Stage.PR_OPENED, Stage.PR_MERGED, Stage.PR_CLOSED):
        pr = payload.get("pr_number")
        return f"PR #{pr}" if pr else None
    if stage == Stage.TRACKED:
        issue = payload.get("issue")
        return f"issue #{issue}" if issue else None
    if stage == Stage.SYNCED:
        return f"{payload.get('from')} → {payload.get('to')}"
    return None


async def _lifecycle_payload(session: AsyncSession, report: FeedbackReport, stage: Stage) -> dict:
    settings = get_settings()
    github_url = (
        f"https://github.com/{settings.github_repo}/issues/{report.github_issue_number}"
        if settings.github_repo and report.github_issue_number
        else None
    )
    events = (
        (
            await session.execute(
                select(FeedbackEvent)
                .where(FeedbackEvent.report_id == report.id)
                .order_by(FeedbackEvent.id.desc())
                .limit(_TIMELINE_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    timeline = [
        {
            "stage": e.stage,
            "at": e.created_at.isoformat() if e.created_at else None,
            "note": _timeline_note(e.stage, e.payload),
        }
        for e in reversed(events)
    ]
    return {
        "report_id": report.id,
        "type": report.type,
        "title": report.title,
        "status": str(report.status),
        "stage": stage.value,
        "github_url": github_url,
        "timeline": timeline,
        "ping": stage in LIFECYCLE_PING_STAGES,
    }


async def notify_stage(session: AsyncSession, report: FeedbackReport, stage: Stage) -> int:
    """Update every linked admin's anchor card for one lifecycle stage
    (ping stages also send a reply). Fully best-effort: any failure logs
    and returns — the loop itself must never depend on Telegram. Returns
    successful sends. Call AFTER the stage's own commit."""
    try:
        settings = get_settings()
        if not settings.bot_base_url or not settings.internal_api_token:
            return 0
        recipients = await _recipients(session)
        if not recipients:
            return 0
        payload = await _lifecycle_payload(session, report, stage)
        anchors = {
            row.tg_user_id: row
            for row in (
                await session.execute(
                    select(FeedbackNotification).where(FeedbackNotification.report_id == report.id)
                )
            )
            .scalars()
            .all()
        }
        sent = 0
        dirty = False
        async with httpx.AsyncClient(timeout=10) as client:
            for tg_user_id in recipients:
                anchor = anchors.get(tg_user_id)
                body = {
                    **payload,
                    "tg_user_id": tg_user_id,
                    "message_id": anchor.message_id if anchor else None,
                }
                try:
                    resp = await client.post(
                        f"{settings.bot_base_url.rstrip('/')}/internal/feedback-lifecycle",
                        json=body,
                        headers={"X-Internal-Token": settings.internal_api_token},
                    )
                    resp.raise_for_status()
                    message_id = resp.json().get("message_id")
                except (httpx.HTTPError, ValueError):
                    logger.warning(
                        "lifecycle notify failed (report #%s, tg %s)", report.id, tg_user_id
                    )
                    continue
                sent += 1
                if message_id and (anchor is None or anchor.message_id != message_id):
                    if anchor is None:
                        session.add(
                            FeedbackNotification(
                                report_id=report.id,
                                tg_user_id=tg_user_id,
                                message_id=message_id,
                            )
                        )
                    else:
                        anchor.message_id = message_id
                    dirty = True
        if dirty:
            await session.commit()
        return sent
    except Exception:  # noqa: BLE001 — notifications must never break the loop
        logger.warning("lifecycle notify errored (report #%s)", report.id, exc_info=True)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
