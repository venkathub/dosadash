"""Internal sentinel incident intake (Phase 15 S4, docs/15).

The deploy canary (and future external deterministic detectors) file
incidents here instead of reimplementing filing: the body names a detector
from an ALLOWLIST (`SentinelIncidentIn.kind`) and the report rides the
exact sentinel spine — SYSTEM report, fingerprint dedupe + daily cap,
evidence redacted inside the UNTRUSTED fence, GitHub mirror best-effort,
triage → NEEDS_APPROVAL (SYSTEM never auto-fixes) → Telegram card.

Auth: X-Internal-Token (fixer-runs pattern). The caller is CI — filing
must be cheap and idempotent-ish (dedupe collapses repeats), and a filing
failure must never mask the rollback that triggered it (caller treats
this as best-effort)."""

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.session import get_session
from dosadash_api.services import sentinel
from dosadash_api.services.github_client import GitHubClient, get_github_client
from dosadash_shared import SentinelIncidentIn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal:sentinel"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
GitHubDep = Annotated[GitHubClient, Depends(get_github_client)]


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/api/v1/internal/sentinel/incident", status_code=201)
async def file_incident(
    body: SentinelIncidentIn,
    session: SessionDep,
    github: GitHubDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> dict:
    _check_internal_token(x_internal_token)
    settings = get_settings()
    anomaly = sentinel.Anomaly(
        kind=body.kind,
        subject=body.subject,
        title=body.title,
        evidence=body.evidence,
    )
    result = await sentinel.file_anomalies(
        session,
        github,
        [anomaly],
        env=settings.env,
        max_per_day=settings.sentinel_max_filings_per_day,
    )
    logger.info("internal sentinel incident %s: %s", anomaly.fingerprint, result)
    return result
