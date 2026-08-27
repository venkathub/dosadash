"""Fixer/verifier metrics (Phase 14 slice 3) — the /fixer portal's numbers.

Everything derives from the three lifecycle tables (feedback_reports,
feedback_events, fixer_runs); no GitHub round-trips. Feedback volume is
tiny (human-filed reports), so the rollup loads the window's rows and
computes in Python — `summarize()` is pure and unit-tested, `compute()` is
the thin async loader.

Honesty rules:
- rates with an empty denominator are None, never a fake 0%;
- latency percentiles use first-occurrence event pairs per report and
  report their sample count alongside p50/p90;
- weekly buckets are IST (restaurant-local, reports convention).
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport, FixerRun
from dosadash_api.services import feedback_autonomy
from dosadash_shared import (
    SENTINEL_FP_MIN_SAMPLES,
    FeedbackEventStage,
    FeedbackMetricsOut,
    FeedbackStatus,
)

IST = ZoneInfo("Asia/Kolkata")

Stage = FeedbackEventStage
Status = FeedbackStatus

# Funnel = distinct reports that ever reached each stage (within window).
_FUNNEL_STAGES: tuple[Stage, ...] = (
    Stage.RECEIVED,
    Stage.TRACKED,
    Stage.TRIAGED,
    Stage.APPROVED,
    Stage.REJECTED,
    Stage.FIX_STARTED,
    Stage.ESCALATED,
    Stage.FIX_FAILED,
    Stage.PR_OPENED,
    Stage.PR_MERGED,
    Stage.FIXED,
    Stage.VERIFIED,
    Stage.REOPENED,
    Stage.DISMISSED,
)

# Latency metrics: (name, from-stages, to-stages) — first occurrence each.
_LATENCY_PAIRS: tuple[tuple[str, tuple[Stage, ...], tuple[Stage, ...]], ...] = (
    ("time_to_triage", (Stage.RECEIVED,), (Stage.TRIAGED,)),
    ("approval_latency", (Stage.TRIAGED,), (Stage.APPROVED, Stage.REJECTED)),
    ("fix_to_pr", (Stage.FIX_STARTED,), (Stage.PR_OPENED,)),
    ("pr_to_merge", (Stage.PR_OPENED,), (Stage.PR_MERGED, Stage.FIXED)),
    ("mttr_received_to_verified", (Stage.RECEIVED,), (Stage.VERIFIED,)),
)


def percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest-rank-with-interpolation percentile; None on empty."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# S6: sentinel detector precision. A decided SYSTEM report is either a
# false positive (a human threw it away) or a true positive (a human acted
# on it). Undecided reports are pending, not evidence; below the sample
# floor the rate is honestly None — ten dismissals mean something, one
# means nothing.
_SENTINEL_FP_STATUSES = frozenset({Status.REJECTED.value, Status.DISMISSED.value})
_SENTINEL_TP_STATUSES = frozenset(
    {
        Status.APPROVED.value,
        Status.FIXING.value,
        Status.PR_OPEN.value,
        Status.FIXED.value,
        Status.VERIFIED.value,
        Status.REOPENED.value,
    }
)


def _sentinel_fp_rate(reports: list[FeedbackReport]) -> float | None:
    system = [r for r in reports if str(r.reporter_tier) == "SYSTEM"]
    fp = sum(1 for r in system if str(r.status) in _SENTINEL_FP_STATUSES)
    tp = sum(1 for r in system if str(r.status) in _SENTINEL_TP_STATUSES)
    decided = fp + tp
    if decided < SENTINEL_FP_MIN_SAMPLES:
        return None
    return round(fp / decided, 4)


def _week_key(dt: datetime) -> str:
    """ISO-Monday of the IST week the (naive-UTC) timestamp falls in."""
    ist = dt.replace(tzinfo=UTC).astimezone(IST)
    monday = ist.date() - timedelta(days=ist.weekday())
    return monday.isoformat()


def summarize(
    reports: list[FeedbackReport],
    events: list[FeedbackEvent],
    runs: list[FixerRun],
    *,
    window_days: int,
    now: datetime | None = None,
    autonomy: dict | None = None,
) -> FeedbackMetricsOut:
    now = now or datetime.now(UTC).replace(tzinfo=None)

    totals_by_status = Counter(str(r.status) for r in reports)
    totals_by_type = Counter(str(r.type) for r in reports)
    totals_by_tier = Counter(str(r.reporter_tier) for r in reports)

    report_ids = {r.id for r in reports}
    # first occurrence of each stage per report
    first_at: dict[int, dict[str, datetime]] = defaultdict(dict)
    for event in events:
        if event.report_id not in report_ids or event.created_at is None:
            continue
        first_at[event.report_id].setdefault(event.stage, event.created_at)

    funnel = {
        stage.value.lower(): sum(1 for stages in first_at.values() if stage.value in stages)
        for stage in _FUNNEL_STAGES
    }
    funnel["mirror_failures"] = sum(1 for r in reports if r.github_error)

    # verdict split from TRIAGED payloads (fallback flag included)
    verdicts = Counter()
    fallbacks = 0
    for event in events:
        if event.stage == Stage.TRIAGED and event.report_id in report_ids:
            payload = event.payload or {}
            verdicts[payload.get("verdict") or "?"] += 1
            if payload.get("fallback"):
                fallbacks += 1

    triaged = funnel["triaged"]
    decided = funnel["approved"] + funnel["rejected"]
    fixed_ever = sum(
        1
        for stages in first_at.values()
        if Stage.PR_MERGED.value in stages or Stage.FIXED.value in stages
    )
    fix_runs = [r for r in runs if r.workflow == "fix"]
    # Phase 15 S7: within-run prompt-cache share, over fix runs that
    # actually reported usage (execution-file parse is best-effort — a
    # window with zero telemetry reports None, never a fake 0%).
    usage_runs = [r for r in fix_runs if r.cache_read_tokens is not None]
    cached = sum(r.cache_read_tokens or 0 for r in usage_runs)
    total_input = sum(
        (r.cache_read_tokens or 0) + (r.cache_creation_tokens or 0) + (r.input_tokens or 0)
        for r in usage_runs
    )
    rates: dict[str, float | None] = {
        "auto_fix_rate": _rate(verdicts.get("AUTO_FIX", 0), triaged),
        "approval_rate": _rate(funnel["approved"], decided),
        "escalation_rate": _rate(funnel["escalated"], funnel["fix_started"]),
        "fix_run_success_rate": _rate(
            sum(1 for r in fix_runs if r.conclusion == "success"), len(fix_runs)
        ),
        "merge_rate": _rate(fixed_ever, funnel["fix_started"]),
        "verification_rate": _rate(funnel["verified"], fixed_ever),
        "reopen_rate": _rate(funnel["reopened"], fixed_ever),
        "triage_fallback_rate": _rate(fallbacks, triaged),
        "fix_cached_token_share": _rate(cached, total_input),
        "sentinel_fp_rate": _sentinel_fp_rate(reports),
    }

    latency: dict[str, dict[str, float | None]] = {}
    for name, from_stages, to_stages in _LATENCY_PAIRS:
        samples: list[float] = []
        for stages in first_at.values():
            start = min((stages[s.value] for s in from_stages if s.value in stages), default=None)
            end = min((stages[s.value] for s in to_stages if s.value in stages), default=None)
            if start is not None and end is not None and end >= start:
                samples.append((end - start).total_seconds())
        samples.sort()
        latency[name] = {
            "p50": percentile(samples, 0.5),
            "p90": percentile(samples, 0.9),
            "count": len(samples),
        }

    weekly_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"reports": 0, "fixed": 0, "verified": 0}
    )
    for report in reports:
        if report.created_at is not None:
            weekly_buckets[_week_key(report.created_at)]["reports"] += 1
    for stages in first_at.values():
        merged_at = stages.get(Stage.PR_MERGED.value) or stages.get(Stage.FIXED.value)
        if merged_at is not None:
            weekly_buckets[_week_key(merged_at)]["fixed"] += 1
        verified_at = stages.get(Stage.VERIFIED.value)
        if verified_at is not None:
            weekly_buckets[_week_key(verified_at)]["verified"] += 1
    weekly = [{"week": week, **counts} for week, counts in sorted(weekly_buckets.items())]

    runs_summary: dict[str, dict[str, int]] = {}
    for run in runs:
        bucket = runs_summary.setdefault(run.workflow, {"total": 0})
        bucket["total"] += 1
        bucket[run.conclusion] = bucket.get(run.conclusion, 0) + 1

    # Phase 15 S7: loop TCO as a number, not a vibe. None when no run in
    # the window carried cost telemetry (empty-denominator honesty rule).
    spend: dict[str, float | None] = {}
    for workflow in ("fix", "verify"):
        costs = [r.cost_usd for r in runs if r.workflow == workflow and r.cost_usd is not None]
        spend[f"{workflow}_cost_usd"] = round(sum(costs), 4) if costs else None
    all_costs = [v for v in spend.values() if v is not None]
    spend["total_cost_usd"] = round(sum(all_costs), 4) if all_costs else None

    return FeedbackMetricsOut(
        window_days=window_days,
        totals_by_status=dict(totals_by_status),
        totals_by_type=dict(totals_by_type),
        totals_by_tier=dict(totals_by_tier),
        funnel=funnel,
        rates=rates,
        latency=latency,
        weekly=weekly,
        runs=runs_summary,
        spend=spend,
        autonomy=autonomy,
        generated_at=now,
    )


async def compute(session: AsyncSession, *, window_days: int = 90) -> FeedbackMetricsOut:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=window_days)
    reports = (
        (await session.execute(select(FeedbackReport).where(FeedbackReport.created_at >= cutoff)))
        .scalars()
        .all()
    )
    events: list[FeedbackEvent] = []
    if reports:
        events = (
            (
                await session.execute(
                    select(FeedbackEvent)
                    .where(FeedbackEvent.report_id.in_([r.id for r in reports]))
                    .order_by(FeedbackEvent.id)
                )
            )
            .scalars()
            .all()
        )
    runs = (
        (await session.execute(select(FixerRun).where(FixerRun.created_at >= cutoff)))
        .scalars()
        .all()
    )
    autonomy = await feedback_autonomy.compute(session)
    return summarize(
        list(reports), list(events), list(runs), window_days=window_days, autonomy=autonomy
    )
