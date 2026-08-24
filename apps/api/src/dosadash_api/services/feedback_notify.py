"""Telegram admin notifications for feedback approvals (Phase 13 slice 4).

Mirror of po_notify.py: the api resolves recipients (linked admin/owner
accounts), builds the card payload, and calls the bot once per recipient —
best-effort, a bot outage never fails triage. Unconfigured bot/token or
zero linked admins → 0 sends (the admin web tab remains the fallback)."""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport, User
from dosadash_shared import Role

logger = logging.getLogger(__name__)


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
