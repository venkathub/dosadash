"""Feedback metrics rollup (Phase 14 slice 3): funnel, honest rates,
latency percentiles, weekly IST buckets, run outcomes — plus RBAC."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport, FixerRun, User
from dosadash_api.services import feedback_metrics
from dosadash_api.services.feedback_metrics import percentile, summarize
from dosadash_shared import Role

T0 = datetime(2026, 8, 20, 6, 0, 0)  # naive UTC, DB convention


def _report(rid: int, **overrides) -> FeedbackReport:
    base = dict(
        id=rid,
        reporter_tier="ANON",
        type="BUG",
        status="TRACKED",
        title=f"r{rid}",
        description="d",
        dedupe_hash=f"{rid:064d}",
        github_issue_number=100 + rid,
        created_at=T0,
    )
    base.update(overrides)
    return FeedbackReport(**base)


def _event(rid: int, stage: str, minutes: int, payload: dict | None = None) -> FeedbackEvent:
    return FeedbackEvent(
        report_id=rid, stage=stage, created_at=T0 + timedelta(minutes=minutes), payload=payload
    )


def _run(workflow: str = "fix", conclusion: str = "success", run_id: int = 1) -> FixerRun:
    return FixerRun(
        workflow=workflow,
        run_id=run_id,
        run_attempt=1,
        conclusion=conclusion,
        created_at=T0,
    )


# ------------------------------------------------------------- percentile


def test_percentile_empty_and_single() -> None:
    assert percentile([], 0.5) is None
    assert percentile([42.0], 0.9) == 42.0


def test_percentile_interpolates() -> None:
    assert percentile([0.0, 10.0], 0.5) == 5.0
    assert percentile([0.0, 10.0, 20.0, 30.0, 40.0], 0.9) == 36.0


# -------------------------------------------------------------- summarize


def _full_loop_fixture() -> tuple[list, list, list]:
    """Report 1 goes the full distance; report 2 dies at rejection;
    report 3 auto-fixes but the run fails."""
    reports = [
        _report(1, status="VERIFIED", type="BUG", reporter_tier="CUSTOMER"),
        _report(2, status="REJECTED", type="FEATURE"),
        _report(3, status="FIXING", github_error="label apply failed: boom"),
    ]
    events = [
        _event(1, "RECEIVED", 0),
        _event(1, "TRACKED", 1),
        _event(1, "TRIAGED", 10, {"verdict": "NEEDS_APPROVAL"}),
        _event(1, "APPROVED", 40),
        _event(1, "FIX_STARTED", 41),
        _event(1, "PR_OPENED", 61),
        _event(1, "PR_MERGED", 121),
        _event(1, "VERIFIED", 180),
        _event(2, "RECEIVED", 0),
        _event(2, "TRIAGED", 20, {"verdict": "NEEDS_APPROVAL", "fallback": True}),
        _event(2, "REJECTED", 50),
        _event(3, "RECEIVED", 0),
        _event(3, "TRIAGED", 30, {"verdict": "AUTO_FIX"}),
        _event(3, "FIX_STARTED", 31),
        _event(3, "FIX_FAILED", 45),
    ]
    runs = [
        _run(run_id=1),
        _run(conclusion="failure", run_id=2),
        _run(workflow="verify", run_id=3),
    ]
    return reports, events, runs


def test_summarize_funnel_and_totals() -> None:
    reports, events, runs = _full_loop_fixture()
    out = summarize(reports, events, runs, window_days=90, now=T0 + timedelta(days=1))
    assert out.totals_by_status == {"VERIFIED": 1, "REJECTED": 1, "FIXING": 1}
    assert out.totals_by_type == {"BUG": 2, "FEATURE": 1}
    assert out.totals_by_tier == {"CUSTOMER": 1, "ANON": 2}
    assert out.funnel["received"] == 3
    assert out.funnel["triaged"] == 3
    assert out.funnel["fix_started"] == 2
    assert out.funnel["pr_merged"] == 1
    assert out.funnel["verified"] == 1
    assert out.funnel["fix_failed"] == 1
    assert out.funnel["mirror_failures"] == 1


def test_summarize_rates_are_honest() -> None:
    reports, events, runs = _full_loop_fixture()
    out = summarize(reports, events, runs, window_days=90, now=T0 + timedelta(days=1))
    assert out.rates["auto_fix_rate"] == round(1 / 3, 4)
    assert out.rates["approval_rate"] == 0.5  # 1 approved / 2 decided
    assert out.rates["fix_run_success_rate"] == 0.5  # verify runs excluded
    assert out.rates["merge_rate"] == 0.5  # 1 merged / 2 fix_started
    assert out.rates["verification_rate"] == 1.0
    assert out.rates["reopen_rate"] == 0.0
    assert out.rates["triage_fallback_rate"] == round(1 / 3, 4)


def test_summarize_empty_denominators_are_none() -> None:
    out = summarize([], [], [], window_days=90)
    assert out.rates["auto_fix_rate"] is None
    assert out.rates["merge_rate"] is None
    assert out.latency["mttr_received_to_verified"]["p50"] is None
    assert out.weekly == []
    assert out.runs == {}


def test_summarize_latency_pairs() -> None:
    reports, events, runs = _full_loop_fixture()
    out = summarize(reports, events, runs, window_days=90, now=T0 + timedelta(days=1))
    # time to triage: 10, 20, 30 min → p50 = 20 min
    assert out.latency["time_to_triage"]["p50"] == 20 * 60
    assert out.latency["time_to_triage"]["count"] == 3
    # approval latency: report1 40-10=30, report2 50-20=30
    assert out.latency["approval_latency"]["p50"] == 30 * 60
    # fix→PR only report 1: 20 min
    assert out.latency["fix_to_pr"]["p50"] == 20 * 60
    assert out.latency["fix_to_pr"]["count"] == 1
    # MTTR received→verified: 180 min
    assert out.latency["mttr_received_to_verified"]["p50"] == 180 * 60


def test_summarize_weekly_and_runs() -> None:
    reports, events, runs = _full_loop_fixture()
    out = summarize(reports, events, runs, window_days=90, now=T0 + timedelta(days=1))
    assert len(out.weekly) == 1
    week = out.weekly[0]
    assert week["reports"] == 3 and week["fixed"] == 1 and week["verified"] == 1
    assert out.runs == {
        "fix": {"total": 2, "success": 1, "failure": 1},
        "verify": {"total": 1, "success": 1},
    }


# ------------------------------------------------------------ endpoint


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def test_metrics_endpoint_rbac_and_shape(client, db_session: AsyncSession) -> None:
    headers = await _login(client, "9111179901")
    resp = await client.get("/api/v1/admin/feedback/metrics", headers=headers)
    assert resp.status_code == 403  # customer

    user = (
        await db_session.execute(select(User).where(User.phone.contains("9111179901")))
    ).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()

    resp = await client.get("/api/v1/admin/feedback/metrics?days=30", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 30
    for key in ("funnel", "rates", "latency", "weekly", "runs", "generated_at"):
        assert key in body


async def test_compute_loads_window(db_session: AsyncSession) -> None:
    recent = _report(11)
    db_session.add(recent)
    await db_session.flush()
    db_session.add(_event(11, "RECEIVED", 0))
    db_session.add(_run(run_id=99))
    await db_session.commit()
    out = await feedback_metrics.compute(db_session, window_days=3650)
    assert out.totals_by_status.get("TRACKED") == 1
    assert out.funnel["received"] == 1
    assert out.runs["fix"]["total"] == 1


# ------------------------------------------------------- S7 usage telemetry


def test_summarize_spend_and_cache_share() -> None:
    """Phase 15 S7: cached-token share over fix runs THAT reported usage;
    spend sums per workflow. 90k cached of 100k total input → 0.9."""
    runs = [
        _run("fix", "success", 1),  # no telemetry — excluded from the share
        FixerRun(
            workflow="fix",
            run_id=2,
            run_attempt=1,
            conclusion="success",
            created_at=T0,
            cost_usd=0.62,
            input_tokens=1000,
            cache_read_tokens=90_000,
            cache_creation_tokens=9_000,
            output_tokens=4_000,
        ),
        FixerRun(
            workflow="verify",
            run_id=3,
            run_attempt=1,
            conclusion="success",
            created_at=T0,
            cost_usd=0.05,
        ),
    ]
    out = summarize([], [], runs, window_days=90, now=T0)
    assert out.rates["fix_cached_token_share"] == 0.9
    assert out.spend["fix_cost_usd"] == 0.62
    assert out.spend["verify_cost_usd"] == 0.05
    assert out.spend["total_cost_usd"] == 0.67


def test_summarize_spend_honest_none_without_telemetry() -> None:
    """No run carried usage → None everywhere, never a fake $0 / 0%."""
    out = summarize([], [], [_run("fix", "success", 1)], window_days=90, now=T0)
    assert out.rates["fix_cached_token_share"] is None
    assert out.spend == {
        "fix_cost_usd": None,
        "verify_cost_usd": None,
        "review_cost_usd": None,
        "total_cost_usd": None,
    }


# --------------------------------------------------- S6 sentinel FP + autonomy


def _system_report(status: str, n: int) -> FeedbackReport:
    return FeedbackReport(
        id=9000 + n,
        reporter_tier="SYSTEM",
        type="BUG",
        status=status,
        title=f"sentinel {n}",
        description="evidence",
        dedupe_hash=str(n).ljust(64, "f")[:64],
        created_at=T0,
    )


def test_sentinel_fp_rate_needs_a_sample_floor() -> None:
    """9 decided SYSTEM reports → None (one dismissal means nothing);
    at the floor the rate is real: 3 FP of 10 decided = 0.3."""
    reports = [_system_report("DISMISSED", i) for i in range(3)] + [
        _system_report("APPROVED", 10 + i) for i in range(6)
    ]
    out = summarize(reports, [], [], window_days=90, now=T0)
    assert out.rates["sentinel_fp_rate"] is None  # 9 decided < floor 10

    reports.append(_system_report("VERIFIED", 30))
    out = summarize(reports, [], [], window_days=90, now=T0)
    assert out.rates["sentinel_fp_rate"] == 0.3


def test_sentinel_fp_ignores_pending_and_human_reports() -> None:
    reports = [_system_report("NEEDS_APPROVAL", i) for i in range(20)]  # all pending
    out = summarize(reports, [], [], window_days=90, now=T0)
    assert out.rates["sentinel_fp_rate"] is None


def test_summarize_passes_autonomy_through() -> None:
    autonomy = {"max_auto_effort": "S", "merged_fixes": 3, "verification_rate": None}
    out = summarize([], [], [], window_days=90, now=T0, autonomy=autonomy)
    assert out.autonomy == autonomy
    out = summarize([], [], [], window_days=90, now=T0)
    assert out.autonomy is None
