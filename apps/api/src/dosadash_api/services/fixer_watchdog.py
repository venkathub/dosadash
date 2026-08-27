"""Fixer dispatch watchdog (post-Phase-14) — GitHub downtime resilience.

Why this exists (postmortem 2026-08-26): a fixer dispatch is just a LABEL
on a GitHub issue — everything after it (queue, runner, agent) is GitHub's
compute. During the "Incident with Actions" major outage the loop did
everything right (intake → triage → owner approval → ai:approved label →
FIX_STARTED event) and then stalled invisibly: one run sat `queued`
forever, a second died with `startup_failure` (zero jobs, zero logs — so
neither the run-ingest step nor any webhook ever fired). Status said
FIXING/APPROVED; nothing was fixing anything.

The watchdog makes that state first-class:

- OBSERVE: list the fix workflow's recent runs + the public
  githubstatus.com Actions component (best-effort, cached).
- COMPUTE (dish-QC philosophy — pure, unit-tested `classify`/`decide`):
  a dispatched report with no live run past the stall window is STALLED
  (reason: run_queued / run_died / dispatch_lost).
- ACT: record a FIX_STALLED timeline event (deduped — one ping per stall,
  not one per beat) and, once Actions is healthy again, RESUME the job by
  re-applying the trigger label (a fresh `labeled` event re-dispatches the
  workflow; the webhook records FIX_STARTED as usual). Stuck-queued runs
  are cancelled first when the token allows (`actions:write`) — a queued
  run that survives recovery would otherwise race the redispatch.

Safety rails:
- outage in progress → transparency only, no redispatch (it would just
  queue another dead run).
- retries capped (MAX_RETRIES) — a persistently dying dispatch escalates
  to a terminal `retries_exhausted` stall instead of looping forever.
- everything is best-effort per report (per-report commits, sync_github
  pattern); the beat never throws away the queue on one bad row.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport
from dosadash_api.services import feedback_events, feedback_notify
from dosadash_api.services.github_client import GitHubClient, GitHubError
from dosadash_shared import (
    FIXER_TRIGGER_LABELS,
    FIXER_WORKFLOW_FILE,
    LABEL_AI_APPROVED,
    LABEL_AI_AUTO_FIX,
    FeedbackEventStage,
    FeedbackStatus,
)

logger = logging.getLogger(__name__)

Stage = FeedbackEventStage
Status = FeedbackStatus

# A dispatch with no live run after this long is stalled. GitHub-hosted
# runners normally pick up in seconds; 10 minutes is far outside normal
# queueing and far inside "the owner is waiting and confused".
STALL_AFTER_MINUTES = 10

# Redispatch attempts per report before the watchdog stops (terminal
# `retries_exhausted` stall — a human takes over from the portal).
MAX_RETRIES = 3

# Run conclusions that mean "the orchestrator never ran our job" — these
# produce no webhook and no run-ingest row (the workflow's own report step
# never executed), so only the watchdog can see them.
DEAD_CONCLUSIONS = frozenset({"startup_failure", "cancelled", "stale", "action_required"})

# Report statuses that mean "a fixer dispatch is (supposedly) in flight".
WATCHDOG_STATUSES = (Status.AUTO_FIX, Status.APPROVED, Status.FIXING)

# Grace applied before the dispatch timestamp when matching runs (label
# event → run creation ordering jitter).
_DISPATCH_GRACE = timedelta(seconds=120)

# githubstatus.com public API — no auth, no token, cached in-process.
GITHUB_STATUS_URL = "https://www.githubstatus.com/api/v2/summary.json"
_STATUS_CACHE_SECONDS = 240
_status_cache: tuple[float, dict[str, Any] | None] | None = None


async def fetch_actions_status(*, force: bool = False) -> dict[str, Any] | None:
    """Live GitHub Actions component status (best-effort, cached).

    Returns {"status": "operational"|"degraded_performance"|…,
    "incident": <active incident name or None>, "checked_at": iso} — or
    None when githubstatus.com itself is unreachable (unknown is unknown,
    never guessed)."""
    global _status_cache
    now = time.monotonic()
    if not force and _status_cache is not None and now - _status_cache[0] < _STATUS_CACHE_SECONDS:
        return _status_cache[1]
    result: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(GITHUB_STATUS_URL)
        resp.raise_for_status()
        data = resp.json()
        component = next(
            (c for c in data.get("components", []) if c.get("name") == "Actions"), None
        )
        if component is not None:
            incident = next(
                (
                    i.get("name")
                    for i in data.get("incidents", [])
                    if any(c.get("name") == "Actions" for c in i.get("components", []))
                ),
                None,
            )
            result = {
                "status": component.get("status"),
                "incident": incident,
                "checked_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            }
    except Exception:  # noqa: BLE001 — best-effort probe: any failure
        # (network, non-JSON body, unexpected shape) is "unknown", never
        # an exception that aborts a watchdog pass.
        logger.warning("githubstatus fetch failed — Actions health unknown")
        result = None
    _status_cache = (now, result)
    return result


def _parse_run_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


def classify(
    runs: list[dict[str, Any]],
    *,
    dispatched_at: datetime,
    now: datetime,
    stall_after_minutes: int = STALL_AFTER_MINUTES,
) -> tuple[str, dict[str, Any]]:
    """Pure verdict for one dispatched report given the fix workflow's
    recent runs (global evidence — the workflow is single-flight by
    concurrency group, so run→report attribution by time window is sound).

    Verdicts: RUNNING (leave it alone) · WAITING (inside the stall
    window) · SETTLED (a run completed normally — ingest/labels own the
    outcome) · STALLED (reason: run_queued / run_died / dispatch_lost)."""
    window_start = dispatched_at - _DISPATCH_GRACE
    relevant = [
        r
        for r in runs
        if (created := _parse_run_time(r.get("created_at"))) is not None and created >= window_start
    ]
    if any(r.get("status") == "in_progress" for r in relevant):
        return "RUNNING", {}
    stalled_for = now - dispatched_at
    stall_window = timedelta(minutes=stall_after_minutes)

    queued = [r for r in relevant if r.get("status") == "queued"]
    if queued:
        oldest = min(
            (_parse_run_time(r.get("created_at")) or now for r in queued),
        )
        if now - oldest >= stall_window:
            run = min(queued, key=lambda r: r.get("id") or 0)
            return "STALLED", {
                "reason": "run_queued",
                "run_id": run.get("id"),
                "queued_minutes": int((now - oldest).total_seconds() // 60),
            }
        return "WAITING", {}

    completed = [r for r in relevant if r.get("status") == "completed"]
    if any(r.get("conclusion") not in DEAD_CONCLUSIONS for r in completed):
        # a run genuinely executed (success or real failure): the run
        # ingest / webhook flow owns what happens next.
        return "SETTLED", {}
    if stalled_for < stall_window:
        return "WAITING", {}
    if completed:
        run = max(completed, key=lambda r: r.get("id") or 0)
        return "STALLED", {
            "reason": "run_died",
            "run_id": run.get("id"),
            "conclusion": run.get("conclusion"),
        }
    return "STALLED", {"reason": "dispatch_lost"}


def decide(
    verdict: str,
    evidence: dict[str, Any],
    *,
    gh_operational: bool | None,
    retries: int,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Pure action policy: NONE | RECORD_STALL | REDISPATCH |
    CANCEL_AND_REDISPATCH.

    - not stalled → NONE (nothing to say, nothing to do)
    - retries exhausted → RECORD_STALL (terminal; humans take over)
    - Actions outage confirmed → RECORD_STALL (transparency only — a
      redispatch now would just queue another dead run)
    - unknown health (None) counts as operational: the status API being
      down must not freeze recovery forever
    - stuck-queued runs are cancelled before redispatching (a queued run
      that wakes up post-recovery would race the fresh dispatch)."""
    if verdict != "STALLED":
        return "NONE"
    if retries >= max_retries:
        return "RECORD_STALL"
    if gh_operational is False:
        return "RECORD_STALL"
    if evidence.get("reason") == "run_queued":
        return "CANCEL_AND_REDISPATCH"
    return "REDISPATCH"


async def _dispatch_history(
    session: AsyncSession, report_id: int
) -> tuple[datetime | None, int, FeedbackEvent | None]:
    """(latest dispatch time, redispatch count, latest watchdog event)."""
    rows = (
        (
            await session.execute(
                select(FeedbackEvent)
                .where(
                    FeedbackEvent.report_id == report_id,
                    FeedbackEvent.stage.in_(
                        [
                            Stage.FIX_STARTED.value,
                            Stage.FIX_RETRIED.value,
                            Stage.FIX_STALLED.value,
                        ]
                    ),
                )
                .order_by(FeedbackEvent.id)
            )
        )
        .scalars()
        .all()
    )
    dispatched_at = None
    retries = 0
    latest_watchdog = None
    for event in rows:
        if event.stage in (Stage.FIX_STARTED.value, Stage.FIX_RETRIED.value):
            dispatched_at = event.created_at
        if event.stage == Stage.FIX_RETRIED.value:
            retries += 1
        if event.stage in (Stage.FIX_STALLED.value, Stage.FIX_RETRIED.value):
            latest_watchdog = event
    return dispatched_at, retries, latest_watchdog


def _trigger_label(issue_labels: list[str], status: Status) -> str:
    """The label to re-apply: whatever trigger the issue carries, else
    derived from the local status (label lost upstream)."""
    for label in FIXER_TRIGGER_LABELS:
        if label in issue_labels:
            return label
    return LABEL_AI_AUTO_FIX if status == Status.AUTO_FIX else LABEL_AI_APPROVED


async def _record_stall(
    session: AsyncSession,
    report: FeedbackReport,
    evidence: dict[str, Any],
    *,
    gh_status: dict[str, Any] | None,
    retries: int,
    latest_watchdog: FeedbackEvent | None,
) -> bool:
    """Append FIX_STALLED unless the latest watchdog event is already an
    identical-reason stall (one ping per stall, not one per beat)."""
    if (
        latest_watchdog is not None
        and latest_watchdog.stage == Stage.FIX_STALLED.value
        and (latest_watchdog.payload or {}).get("reason") == evidence.get("reason")
    ):
        return False
    payload = {
        **evidence,
        "retries": retries,
        "github_actions": gh_status,
    }
    feedback_events.record(session, report, Stage.FIX_STALLED, actor="watchdog", payload=payload)
    await session.commit()
    await feedback_events.publish(report.id, Stage.FIX_STALLED, detail=payload)
    await feedback_notify.notify_stage(session, report, Stage.FIX_STALLED)
    return True


# Session-scoped Postgres advisory lock: two overlapping watchdog passes
# (a slow GitHub can stretch one past the 5-min beat) would both read the
# same retry count and DOUBLE-DISPATCH paid fixer runs. Single-flight is a
# cost control, not a nicety. Key = arbitrary constant, project-unique.
_ADVISORY_LOCK_KEY = 0xD05A_F17E


async def watch(session: AsyncSession, github: GitHubClient, *, limit: int = 20) -> dict[str, Any]:
    """One watchdog pass; per-report commits (sync_github pattern).
    Single-flight via a pg advisory lock — an overlapping pass returns
    immediately instead of double-spending redispatch attempts."""
    if not github.enabled:
        return {"examined": 0, "stalled": 0, "retried": 0, "skipped": 0, "disabled": True}
    locked = await session.scalar(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    )
    if not locked:
        logger.info("watchdog pass already running — skipping (single-flight)")
        return {"examined": 0, "stalled": 0, "retried": 0, "skipped": 0, "overlapped": True}
    try:
        return await _watch_locked(session, github, limit=limit)
    finally:
        try:
            await session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            )
        except Exception:  # noqa: BLE001 — lock dies with the session anyway
            pass


async def _watch_locked(
    session: AsyncSession, github: GitHubClient, *, limit: int = 20
) -> dict[str, Any]:
    reports = (
        (
            await session.execute(
                select(FeedbackReport)
                .where(
                    FeedbackReport.github_issue_number.is_not(None),
                    FeedbackReport.fix_pr_number.is_(None),
                    FeedbackReport.status.in_([s.value for s in WATCHDOG_STATUSES]),
                )
                .order_by(FeedbackReport.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    summary: dict[str, Any] = {"examined": 0, "stalled": 0, "retried": 0, "skipped": 0}
    if not reports:
        return summary

    gh_status = await fetch_actions_status()
    gh_operational = None if gh_status is None else gh_status.get("status") == "operational"
    summary["github_actions"] = gh_status
    try:
        runs = await github.list_workflow_runs(FIXER_WORKFLOW_FILE)
    except GitHubError as exc:
        logger.warning("watchdog: run listing failed — %s", exc)
        summary["skipped"] = len(reports)
        return summary

    now = datetime.now(UTC).replace(tzinfo=None)
    for report in reports:
        # a rollback in a previous iteration expires every loaded row —
        # refresh before touching attributes (async lazy-load would raise).
        if sa_inspect(report).expired:
            try:
                await session.refresh(report)
            except Exception:  # noqa: BLE001
                summary["skipped"] += 1
                continue
        summary["examined"] += 1
        dispatched_at, retries, latest_watchdog = await _dispatch_history(session, report.id)
        if dispatched_at is None:
            # dispatched status but no dispatch event — trust updated_at
            # (an APPROVED row whose label add was lost still deserves
            # watching rather than being invisible forever).
            dispatched_at = report.updated_at or report.created_at or now
        verdict, evidence = classify(runs, dispatched_at=dispatched_at, now=now)
        if retries >= MAX_RETRIES and verdict == "STALLED":
            evidence = {**evidence, "reason": "retries_exhausted", "last": evidence.get("reason")}
        action = decide(verdict, evidence, gh_operational=gh_operational, retries=retries)
        try:
            if action == "RECORD_STALL":
                if await _record_stall(
                    session,
                    report,
                    evidence,
                    gh_status=gh_status,
                    retries=retries,
                    latest_watchdog=latest_watchdog,
                ):
                    summary["stalled"] += 1
            elif action in ("REDISPATCH", "CANCEL_AND_REDISPATCH"):
                outcome: str | None = None
                if action == "CANCEL_AND_REDISPATCH":
                    stuck_run_id = evidence.get("run_id")
                    outcome = (
                        await github.cancel_workflow_run(stuck_run_id)
                        if stuck_run_id is not None
                        else "forbidden"
                    )
                    if outcome == "forbidden":
                        # token lacks actions:write — never dispatch
                        # alongside a run we cannot control.
                        if await _record_stall(
                            session,
                            report,
                            {**evidence, "reason": "cancel_forbidden"},
                            gh_status=gh_status,
                            retries=retries,
                            latest_watchdog=latest_watchdog,
                        ):
                            summary["stalled"] += 1
                        continue
                    # "cancelled" → clean redispatch. "refused" (409) →
                    # the run is finished or OUTAGE-ORPHANED (postmortem:
                    # `queued` forever, uncancellable by any token, never
                    # starts). The fixer workflow's concurrency group
                    # replaces pending runs, so a fresh dispatch is safe
                    # and is the ONLY exit from that zombie state —
                    # holding forever means the fix never lands.
                issue = await github.get_issue(report.github_issue_number)
                label = _trigger_label(issue["labels"], Status(report.status))
                payload = {
                    "attempt": retries + 1,
                    "label": label,
                    "cancelled_run_id": evidence.get("run_id")
                    if action == "CANCEL_AND_REDISPATCH"
                    else None,
                    "cancel_outcome": outcome if action == "CANCEL_AND_REDISPATCH" else None,
                    "reason": evidence.get("reason"),
                }
                # COST-CRITICAL ORDERING: a redispatch launches a PAID
                # fixer run. Record + COMMIT the FIX_RETRIED attempt
                # BEFORE touching the label (Phase-8 batch precedent —
                # local work commits before the provider call), so the
                # MAX_RETRIES cap can never under-count: a crash between
                # commit and dispatch wastes one attempt, never spawns
                # an unaccounted run. The webhook's FIX_STARTED echo is
                # the confirmation the dispatch actually landed.
                feedback_events.record(
                    session, report, Stage.FIX_RETRIED, actor="watchdog", payload=payload
                )
                await session.commit()
                try:
                    await github.remove_label(report.github_issue_number, label)
                    await github.add_labels(report.github_issue_number, [label])
                except GitHubError as exc:
                    # attempt consumed, dispatch failed — the stall will
                    # re-classify next beat and retry with n-1 budget.
                    summary["skipped"] += 1
                    logger.warning(
                        "watchdog redispatch failed for report #%s (attempt %s counted): %s",
                        report.id,
                        retries + 1,
                        exc,
                    )
                    continue
                await feedback_events.publish(report.id, Stage.FIX_RETRIED, detail=payload)
                await feedback_notify.notify_stage(session, report, Stage.FIX_RETRIED)
                summary["retried"] += 1
                logger.info(
                    "watchdog re-dispatched report #%s (attempt %s, label %s)",
                    report.id,
                    retries + 1,
                    label,
                )
        except Exception as exc:  # noqa: BLE001 — per-report isolation:
            # one poisoned row (GitHub error, DB hiccup, bad payload)
            # must never abort recovery for every other report.
            summary["skipped"] += 1
            logger.warning("watchdog skipped report #%s: %s", report.id, exc, exc_info=True)
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass
    return summary


async def current_stalls(session: AsyncSession) -> list[dict[str, Any]]:
    """Latest unresolved stall per in-flight report (portal `ops` feed).

    A stall is superseded by any later FIX_RETRIED / PR_OPENED /
    FIX_FAILED / ESCALATED event — those mean the loop moved again."""
    reports = (
        (
            await session.execute(
                select(FeedbackReport).where(
                    FeedbackReport.status.in_([s.value for s in WATCHDOG_STATUSES])
                )
            )
        )
        .scalars()
        .all()
    )
    stalls: list[dict[str, Any]] = []
    superseding = {
        Stage.FIX_RETRIED.value,
        Stage.PR_OPENED.value,
        Stage.FIX_FAILED.value,
        Stage.ESCALATED.value,
        Stage.FIX_STARTED.value,
    }
    for report in reports:
        events = (
            (
                await session.execute(
                    select(FeedbackEvent)
                    .where(
                        FeedbackEvent.report_id == report.id,
                        FeedbackEvent.stage.in_([Stage.FIX_STALLED.value, *superseding]),
                    )
                    .order_by(FeedbackEvent.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if events is None or events.stage != Stage.FIX_STALLED.value:
            continue
        payload = events.payload or {}
        stalls.append(
            {
                "report_id": report.id,
                "reason": payload.get("reason", "unknown"),
                "run_id": payload.get("run_id"),
                "retries": payload.get("retries", 0),
                "since": events.created_at,
                "detail": payload,
            }
        )
    return stalls
