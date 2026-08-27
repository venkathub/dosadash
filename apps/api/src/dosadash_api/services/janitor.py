"""Maintenance janitor (Phase 15 S5, docs/15) — scheduled hygiene reports.

A weekly beat that files SYSTEM reports for maintenance debt nobody
notices day-to-day. Detection is deterministic (zero LLM — the sentinel
precedent), filing rides the exact sentinel spine (fingerprint dedupe,
UNTRUSTED fence, GitHub issue, triage → NEEDS_APPROVAL → Telegram card;
SYSTEM can never auto-fix).

v1 detectors (all pure, all local DB):
- **flaky eval cases**: tallies per-case failures across the last N
  `eval_runs` case_reports. Flaky = INTERMITTENT (failed ≥2 runs but not
  every run — a case failing every run is broken, and the live gate
  itself owns that). This retires the hand-maintained flaky list in
  CLAUDE.md: the wobble pool becomes a computed artifact.
- **translation backlog**: DRAFT menu translations piling up unreviewed.
- **stale approvals**: feedback reports sitting NEEDS_APPROVAL for over a
  week — the loop's own hygiene. SYSTEM-tier reports are EXCLUDED here
  (they have their own visibility, and counting janitor reports would
  make next week's janitor report itself the finding: self-reference).

Deliberately deferred (docs/15): dependency-update scans (needs network
tooling on the worker; the #90 lesson — lockfile bumps need human RSS
measurement — makes them approval-lane work anyway) and Langfuse cost
drift (needs a costs read model in the worker). Narratives are templated,
not LLM: when an LLM summarizer is ever added it goes Batch-only (S7).
"""

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import Settings, get_settings
from dosadash_api.db.models import EvalRun, FeedbackReport, MenuItemTranslation
from dosadash_api.services import sentinel
from dosadash_api.services.github_client import GitHubClient
from dosadash_shared import FeedbackStatus

logger = logging.getLogger(__name__)

JANITOR_FLAKY_KIND = "janitor_flaky_evals"
JANITOR_TRANSLATION_KIND = "janitor_translation_backlog"
JANITOR_STALE_KIND = "janitor_stale_approvals"

FLAKY_RUNS_WINDOW = 10  # last N ingested eval runs
FLAKY_MIN_FAILS = 2  # a single wobble means nothing (flaky-first policy)
FLAKY_TOP_N = 15
TRANSLATION_BACKLOG_THRESHOLD = 10
STALE_APPROVAL_DAYS = 7
STALE_TOP_N = 10

_ADVISORY_LOCK_KEY = 0x0A17_0B01


def _case_failed(case: dict[str, Any]) -> bool:
    return bool(
        case.get("accuracy_problems") or case.get("tool_violations") or case.get("bypasses")
    )


# ------------------------------------------------------------------ compute


def classify_flaky(runs: list[dict[str, Any]]) -> sentinel.Anomaly | None:
    """runs: most-recent-first [{"id", "case_reports": [...]}]. Flaky =
    failed in ≥FLAKY_MIN_FAILS runs AND passed in at least one — a case
    failing EVERY appearance is broken, not flaky, and the live gate
    already blocks on it."""
    fails: Counter[str] = Counter()
    appearances: Counter[str] = Counter()
    for run in runs:
        for case in run.get("case_reports") or []:
            case_id = case.get("id")
            if not case_id:
                continue
            appearances[case_id] += 1
            if _case_failed(case):
                fails[case_id] += 1
    wobble = {
        case_id: n
        for case_id, n in fails.items()
        if n >= FLAKY_MIN_FAILS and n < appearances[case_id]
    }
    if not wobble:
        return None
    top = dict(sorted(wobble.items(), key=lambda kv: (-kv[1], kv[0]))[:FLAKY_TOP_N])
    return sentinel.Anomaly(
        kind=JANITOR_FLAKY_KIND,
        subject="live-gate",
        title="flaky eval cases accumulating — quarantine review needed",
        evidence={
            "window_runs": len(runs),
            "min_fails": FLAKY_MIN_FAILS,
            "wobbling_cases": top,
            "note": "intermittent only — cases failing every run are the gate's job",
        },
    )


def classify_translation_backlog(
    draft_count: int, *, threshold: int = TRANSLATION_BACKLOG_THRESHOLD
) -> sentinel.Anomaly | None:
    if draft_count < threshold:
        return None
    return sentinel.Anomaly(
        kind=JANITOR_TRANSLATION_KIND,
        subject="menu",
        title="menu translation drafts piling up unreviewed",
        evidence={"draft_count": draft_count, "threshold": threshold},
    )


def classify_stale_approvals(
    pending: list[dict[str, Any]],
    *,
    now: datetime,
    max_age_days: int = STALE_APPROVAL_DAYS,
) -> sentinel.Anomaly | None:
    """pending: [{"id", "title", "created_at"}] — NEEDS_APPROVAL,
    non-SYSTEM (caller filters; janitor must never count its own kind)."""
    cutoff = now - timedelta(days=max_age_days)
    stale = [p for p in pending if p.get("created_at") and p["created_at"] <= cutoff]
    if not stale:
        return None
    stale.sort(key=lambda p: p["created_at"])
    return sentinel.Anomaly(
        kind=JANITOR_STALE_KIND,
        subject="approvals",
        title="feedback reports waiting on approval for over a week",
        evidence={
            "stale_count": len(stale),
            "max_age_days": max_age_days,
            "oldest": [
                {"report_id": p["id"], "title": str(p.get("title"))[:80]}
                for p in stale[:STALE_TOP_N]
            ],
        },
    )


# ------------------------------------------------------------------ observe


async def recent_eval_case_reports(
    session: AsyncSession, *, limit: int = FLAKY_RUNS_WINDOW
) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(select(EvalRun).order_by(EvalRun.ran_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [{"id": r.id, "case_reports": r.case_reports or []} for r in rows]


async def draft_translation_count(session: AsyncSession) -> int:
    return (
        await session.scalar(select(func.count()).where(MenuItemTranslation.status == "DRAFT")) or 0
    )


async def pending_approvals(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(FeedbackReport)
                .where(
                    FeedbackReport.status == FeedbackStatus.NEEDS_APPROVAL.value,
                    # janitor reports are SYSTEM+NEEDS_APPROVAL themselves —
                    # counting them would make the janitor its own finding
                    FeedbackReport.reporter_tier != "SYSTEM",
                )
                .order_by(FeedbackReport.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": r.id, "title": r.title, "created_at": r.created_at} for r in rows]


# ---------------------------------------------------------------------- act


async def scan(
    session: AsyncSession,
    github: GitHubClient,
    *,
    settings: Settings | None = None,
    eval_runs: list[dict[str, Any]] | None = None,
    draft_count: int | None = None,
    pending: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One weekly janitor pass. Signals injectable for tests; None means
    collect live. Advisory-locked (sentinel pattern, distinct key)."""
    settings = settings or get_settings()
    if not settings.sentinel_enabled:  # one switch governs machine reporters
        return {"enabled": False, "anomalies": 0}

    got_lock = await session.scalar(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    )
    if not got_lock:
        return {"skipped": "another janitor pass holds the lock", "anomalies": 0}
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        if eval_runs is None:
            eval_runs = await recent_eval_case_reports(session)
        if draft_count is None:
            draft_count = await draft_translation_count(session)
        if pending is None:
            pending = await pending_approvals(session)

        anomalies = [
            anomaly
            for anomaly in (
                classify_flaky(eval_runs),
                classify_translation_backlog(draft_count),
                classify_stale_approvals(pending, now=now),
            )
            if anomaly is not None
        ]
        result = await sentinel.file_anomalies(
            session,
            github,
            anomalies,
            env=settings.env,
            max_per_day=settings.sentinel_max_filings_per_day,
            now=now,
        )
        result["anomalies"] = len(anomalies)
        return result
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
