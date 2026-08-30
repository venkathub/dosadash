"""Production sentinel — telemetry as a feedback reporter (Phase 15 S1, docs/15).

The literal self-healing upgrade: the platform files its own bug reports
through the EXISTING intake spine (feedback_reports → GitHub issue →
triage → Telegram approval → fixer). Nothing downstream is new — a
sentinel report is just a report whose reporter is the system itself
(`reporter_tier = SYSTEM`).

Design (fixer-watchdog philosophy — observe, compute, act):
- OBSERVE (impure collectors, all best-effort): /healthz probes of
  api/ai/bot over the compose network, the 5xx counter minutes the api
  middleware writes to the cache Redis, recent `eval_runs` rows.
- COMPUTE (pure, unit-tested `classify_*`): deterministic threshold rules.
  ZERO LLM in detection — the triage LLM downstream only summarizes, the
  policy routes, and SYSTEM reports can never AUTO_FIX in v1
  (`feedback_triage.decide`, property-gated).
- ACT: file a report exactly like the intake router does (row first,
  RECEIVED event, GitHub mirror best-effort — hotfix-#72 pattern —
  commit per report, publish + notify after). Evidence goes inside the
  UNTRUSTED fence: log/error strings are attacker-influencable.

Incident dedupe (Sentry-style grouping): the dedupe hash is computed from
`type|title|fingerprint` — NOT the volatile evidence — so a recurring
anomaly collapses onto its open report via the existing indexed mechanism.
Titles are deliberately count-free (counts live in the evidence) so the
hash stays stable. A hard cap (`sentinel_max_filings_per_day` per
fingerprint, any status) makes re-filing after dismissal alerting, not
spam.

Deliberately NOT detected in v1 (docs/15 §S1): fixer-run failures
(FIX_FAILED already covers), beat staleness (the sentinel cannot watch its
own scheduler), Langfuse cost anomalies (v1.5).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import Settings, get_settings
from dosadash_api.db.models import EvalRun, FeedbackReport
from dosadash_api.services import feedback_events, feedback_notify, feedback_service
from dosadash_api.services.github_client import GitHubClient, GitHubError
from dosadash_shared import (
    FeedbackEventStage,
    FeedbackStatus,
    FeedbackType,
    ReporterTier,
    redact_phones,
)

logger = logging.getLogger(__name__)

# Anomaly kinds (fingerprint = "<kind>:<subject>").
ANOMALY_SERVICE_DOWN = "service_down"
ANOMALY_5XX_BURST = "http_5xx_burst"
ANOMALY_EVAL_GATE = "eval_gate_failing"

SENTINEL_ACTOR = "sentinel"

# 5xx counter keys the api middleware writes (sentinel_counters.py):
# sentinel:5xx:<epoch_minute> on the cache Redis. Kept OUTSIDE any
# cascade-flushed prefix; allkeys-lru may evict — running indicators,
# not billing records (usage_stats.py philosophy).
FIVEXX_KEY_PREFIX = "sentinel:5xx:"

# Consecutive failed eval runs before the sentinel alerts. One red run is
# the documented flaky-first reality (re-run before debugging); two
# consecutive reds mean the gate, the provider chain, or the golden set is
# genuinely broken and nobody may be looking.
EVAL_CONSECUTIVE_FAILURES = 2

# Probe retries: a single connection failure during a deploy-time container
# restart is a transient — the same "flaky-first" philosophy as
# EVAL_CONSECUTIVE_FAILURES.  Two attempts with a short delay between them
# absorb restart-window blips; a genuinely-down service fails every attempt.
PROBE_RETRIES = 2
PROBE_RETRY_DELAY_SECONDS = 5.0

# Single-flight advisory lock (watchdog pattern, distinct key).
_ADVISORY_LOCK_KEY = 0x5E17_10E1


@dataclass(frozen=True)
class Anomaly:
    """One detected incident. `title` must be stable across recurrences
    (no counts/timestamps — they go in `evidence`) so the dedupe hash
    collapses repeats onto the open report."""

    kind: str
    subject: str
    title: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return f"{self.kind}:{self.subject}"


# ------------------------------------------------------------------ compute
# Pure classifiers — unit-tested, no I/O, no clock reads.


def classify_health(probes: dict[str, dict[str, Any]]) -> list[Anomaly]:
    """probes: service name → {"ok": bool, "status": int|None, "error": str|None, "url": str}."""
    anomalies: list[Anomaly] = []
    for name in sorted(probes):
        probe = probes[name]
        if probe.get("ok"):
            continue
        anomalies.append(
            Anomaly(
                kind=ANOMALY_SERVICE_DOWN,
                subject=name,
                title=f"{name} service failing healthcheck",
                evidence=dict(probe),
            )
        )
    return anomalies


def classify_5xx(counts: dict[int, int], *, threshold: int, window_minutes: int) -> Anomaly | None:
    """counts: epoch-minute → 5xx count within the window."""
    total = sum(counts.values())
    if total < threshold:
        return None
    return Anomaly(
        kind=ANOMALY_5XX_BURST,
        subject="api",
        title="api 5xx error burst",
        evidence={
            "total_5xx": total,
            "window_minutes": window_minutes,
            "threshold": threshold,
            "per_minute": {str(k): v for k, v in sorted(counts.items())},
        },
    )


def classify_eval_runs(runs: list[dict[str, Any]]) -> Anomaly | None:
    """runs: most-recent-first [{"id", "git_sha", "gates_passed", "order_accuracy"}, …].

    Alerts only on EVAL_CONSECUTIVE_FAILURES straight reds — a single red
    run is the documented flaky-first case (re-run before debugging)."""
    if len(runs) < EVAL_CONSECUTIVE_FAILURES:
        return None
    latest = runs[:EVAL_CONSECUTIVE_FAILURES]
    if any(r.get("gates_passed") for r in latest):
        return None
    sha = str(latest[0].get("git_sha") or latest[0].get("id"))[:12]
    return Anomaly(
        kind=ANOMALY_EVAL_GATE,
        subject=sha,
        title="live eval gate failing on consecutive runs",
        evidence={
            "consecutive_failures": EVAL_CONSECUTIVE_FAILURES,
            "runs": [
                {
                    "id": r.get("id"),
                    "git_sha": r.get("git_sha"),
                    "order_accuracy": r.get("order_accuracy"),
                }
                for r in latest
            ],
        },
    )


def build_description(anomaly: Anomaly, *, checked_at: datetime) -> str:
    """Evidence markdown for the report body (→ UNTRUSTED fence in the
    issue). Redacted (Rule 8 — error strings can echo request payloads),
    capped at the human-intake length so nothing downstream is surprised."""
    evidence = json.dumps(anomaly.evidence, indent=2, sort_keys=True, default=str)
    body = (
        f"Automated sentinel detection `{anomaly.fingerprint}` "
        f"at {checked_at.isoformat()}Z.\n\n"
        f"Evidence snapshot:\n```json\n{evidence}\n```"
    )
    return redact_phones(body)[:2000]


# ------------------------------------------------------------------ observe
# Impure collectors — each degrades to "no signal" on its own failure so
# one broken probe never blinds the others.


async def probe_services(settings: Settings) -> dict[str, dict[str, Any]]:
    targets = {
        "api": settings.self_base_url,
        "ai": settings.ai_base_url,
        "bot": settings.bot_base_url,
    }
    results: dict[str, dict[str, Any]] = {}
    timeout = settings.sentinel_probe_timeout_seconds
    async with httpx.AsyncClient(timeout=timeout) as client:
        for name, base in targets.items():
            if not base:
                continue  # optional service disabled by config (e.g. bot)
            url = base.rstrip("/") + "/healthz"
            last: dict[str, Any] = {"ok": False, "status": None, "error": None, "url": url}
            for attempt in range(PROBE_RETRIES):
                if attempt > 0:
                    await asyncio.sleep(PROBE_RETRY_DELAY_SECONDS)
                try:
                    resp = await client.get(url)
                    last = {
                        "ok": resp.status_code == 200,
                        "status": resp.status_code,
                        "error": None,
                        "url": url,
                    }
                except Exception as exc:  # noqa: BLE001 — unreachable IS the signal
                    last = {
                        "ok": False,
                        "status": None,
                        "error": str(exc)[:200],
                        "url": url,
                    }
                if last["ok"]:
                    break
            results[name] = last
    return results


async def read_5xx_counts(settings: Settings, *, now: datetime) -> dict[int, int]:
    """Read the middleware's per-minute 5xx counters. Redis outage → {}
    (no signal, never a crash — the health probes still run)."""
    window = settings.sentinel_5xx_window_minutes
    current_minute = int(now.timestamp()) // 60
    minutes = list(range(current_minute - window + 1, current_minute + 1))
    try:
        redis: Redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        try:
            values = await redis.mget([f"{FIVEXX_KEY_PREFIX}{m}" for m in minutes])
        finally:
            await redis.aclose()
    except Exception:  # noqa: BLE001 — cache Redis down must not kill the scan
        return {}
    return {m: int(v) for m, v in zip(minutes, values, strict=True) if v}


async def recent_eval_runs(session: AsyncSession, *, limit: int = 2) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(select(EvalRun).order_by(EvalRun.ran_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "git_sha": r.git_sha,
            "gates_passed": r.gates_passed,
            "order_accuracy": r.order_accuracy,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------- act


async def file_anomalies(
    session: AsyncSession,
    github: GitHubClient,
    anomalies: list[Anomaly],
    *,
    env: str,
    max_per_day: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """File each anomaly as a SYSTEM feedback report — the intake router's
    flow, minus the human. Per-anomaly commits (sync_github pattern): one
    bad row never throws away the rest of the scan."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    filed = skipped_open = skipped_capped = mirror_failures = 0

    for anomaly in anomalies:
        title = anomaly.title[:120]
        description = build_description(anomaly, checked_at=now)
        # Dedupe on the STABLE fingerprint, not the volatile evidence —
        # recurring anomalies collapse onto the open report.
        dedupe = feedback_service.compute_dedupe_hash(FeedbackType.BUG, title, anomaly.fingerprint)

        open_twin = await session.scalar(
            select(FeedbackReport.id)
            .where(
                FeedbackReport.dedupe_hash == dedupe,
                FeedbackReport.status.in_([s.value for s in feedback_service.OPEN_STATUSES]),
            )
            .limit(1)
        )
        if open_twin is not None:
            skipped_open += 1
            continue

        # Hard daily cap per fingerprint (ANY status): re-filing after a
        # dismissal is alerting; endless re-filing is spam.
        recent = await session.scalar(
            select(func.count(FeedbackReport.id)).where(
                FeedbackReport.dedupe_hash == dedupe,
                FeedbackReport.created_at >= now - timedelta(hours=24),
            )
        )
        if (recent or 0) >= max_per_day:
            skipped_capped += 1
            continue

        report = FeedbackReport(
            user_id=None,
            reporter_tier=ReporterTier.SYSTEM,
            type=FeedbackType.BUG,
            status=FeedbackStatus.RECEIVED,
            title=title,
            description=description,
            context={"fingerprint": anomaly.fingerprint, "detector": anomaly.kind},
            dedupe_hash=dedupe,
        )
        session.add(report)
        await session.flush()

        stages = [FeedbackEventStage.RECEIVED]
        feedback_events.record(
            session,
            report,
            FeedbackEventStage.RECEIVED,
            actor=SENTINEL_ACTOR,
            payload={"tier": report.reporter_tier, "fingerprint": anomaly.fingerprint},
        )

        if github.enabled:
            try:
                report.github_issue_number = await github.create_issue(
                    title=feedback_service.issue_title(report),
                    body=feedback_service.build_issue_body(report, env=env),
                    labels=feedback_service.issue_labels(report),
                )
                report.status = FeedbackStatus.TRACKED
                feedback_events.record(
                    session,
                    report,
                    FeedbackEventStage.TRACKED,
                    actor=SENTINEL_ACTOR,
                    payload={"issue": report.github_issue_number},
                )
                stages.append(FeedbackEventStage.TRACKED)
            except GitHubError as exc:
                report.github_error = str(exc)[:300]
                mirror_failures += 1
                logger.warning("sentinel report stored; GitHub mirror failed: %s", exc)
        else:
            report.github_error = "github integration disabled (API_GITHUB_TOKEN/REPO unset)"

        await session.commit()
        filed += 1
        for stage in stages:
            await feedback_events.publish(report.id, stage)
        await feedback_notify.notify_stage(session, report, stages[-1])
        logger.info("sentinel filed report #%s (%s)", report.id, anomaly.fingerprint)

    return {
        "filed": filed,
        "skipped_open": skipped_open,
        "skipped_capped": skipped_capped,
        "mirror_failures": mirror_failures,
    }


async def scan(
    session: AsyncSession,
    github: GitHubClient,
    *,
    settings: Settings | None = None,
    probes: dict[str, dict[str, Any]] | None = None,
    counts: dict[int, int] | None = None,
    eval_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One sentinel pass. Signal arguments are injectable for tests; None
    means collect live. Advisory-locked single-flight (watchdog pattern)."""
    settings = settings or get_settings()
    if not settings.sentinel_enabled:
        return {"enabled": False, "anomalies": 0}

    got_lock = await session.scalar(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
    )
    if not got_lock:
        return {"skipped": "another sentinel pass holds the lock", "anomalies": 0}
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        if probes is None:
            probes = await probe_services(settings)
        if counts is None:
            counts = await read_5xx_counts(settings, now=datetime.now(UTC))
        if eval_runs is None:
            eval_runs = await recent_eval_runs(session, limit=EVAL_CONSECUTIVE_FAILURES)

        anomalies = classify_health(probes)
        burst = classify_5xx(
            counts,
            threshold=settings.sentinel_5xx_threshold,
            window_minutes=settings.sentinel_5xx_window_minutes,
        )
        if burst is not None:
            anomalies.append(burst)
        eval_anomaly = classify_eval_runs(eval_runs)
        if eval_anomaly is not None:
            anomalies.append(eval_anomaly)

        result = await file_anomalies(
            session,
            github,
            anomalies,
            env=settings.env,
            max_per_day=settings.sentinel_max_filings_per_day,
            now=now,
        )
        result["anomalies"] = len(anomalies)
        result["probed"] = sorted(probes)
        return result
    finally:
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
