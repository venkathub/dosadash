"""Earned-autonomy (capability ladder) tests — Phase 15 S6, docs/15.

The M rung must be EARNED from measured outcomes: pure boundary tests on
`unlocked()`, DB-backed `compute()`, and the triage runner actually
passing the ceiling into the triage request (and recording it)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport
from dosadash_api.services import feedback_autonomy, feedback_triage_runner
from dosadash_shared import (
    AUTO_FIX_M_MIN_MERGED_FIXES,
    AUTO_FIX_M_MIN_VERIFICATION_RATE,
    FeedbackTriageResponse,
    TriageVerdict,
)

# ---------------------------------------------------------------- pure rule


def test_unlocked_boundaries() -> None:
    threshold = AUTO_FIX_M_MIN_MERGED_FIXES
    rate = AUTO_FIX_M_MIN_VERIFICATION_RATE
    assert not feedback_autonomy.unlocked(threshold - 1, 1.0)  # too few merged
    assert not feedback_autonomy.unlocked(threshold, rate - 0.01)  # rate below floor
    assert not feedback_autonomy.unlocked(threshold, None)  # no concluded verifications
    assert not feedback_autonomy.unlocked(0, None)  # cold start stays locked
    assert feedback_autonomy.unlocked(threshold, rate)  # exact thresholds unlock
    assert feedback_autonomy.unlocked(threshold + 5, 1.0)


# ------------------------------------------------------------------ compute


def _report(status: str, n: int) -> FeedbackReport:
    return FeedbackReport(
        reporter_tier="CUSTOMER",
        type="BUG",
        status=status,
        title=f"report {status} {n}",
        description="ten chars or more",
        dedupe_hash=f"{status}{n}".ljust(64, "0")[:64],
    )


async def _seed(session: AsyncSession, **status_counts: int) -> None:
    i = 0
    for status, count in status_counts.items():
        for _ in range(count):
            i += 1
            session.add(_report(status, i))
    await session.commit()


async def test_compute_locked_by_default(db_session: AsyncSession) -> None:
    state = await feedback_autonomy.compute(db_session)
    assert state["max_auto_effort"] == "S"
    assert state["merged_fixes"] == 0
    assert state["verification_rate"] is None  # honest None, never fake 0/1


async def test_compute_pending_fixed_rows_do_not_count_as_evidence(
    db_session: AsyncSession,
) -> None:
    """FIXED-but-unverified rows count toward merged volume but NOT the
    verification rate — a slow verifier must not look like a bad fixer."""
    await _seed(db_session, FIXED=25)
    state = await feedback_autonomy.compute(db_session)
    assert state["merged_fixes"] == 25
    assert state["verification_rate"] is None
    assert state["max_auto_effort"] == "S"  # volume alone never unlocks


async def test_compute_unlocks_on_earned_record(db_session: AsyncSession) -> None:
    await _seed(db_session, VERIFIED=19, REOPENED=2)  # 21 merged, rate 0.9048
    state = await feedback_autonomy.compute(db_session)
    assert state["merged_fixes"] == 21
    assert state["verification_rate"] == 0.9048
    assert state["max_auto_effort"] == "M"


async def test_compute_reopens_lock_it_back(db_session: AsyncSession) -> None:
    await _seed(db_session, VERIFIED=18, REOPENED=3)  # rate 0.8571 < 0.90
    state = await feedback_autonomy.compute(db_session)
    assert state["max_auto_effort"] == "S"


# ---------------------------------------------------------- runner ceiling


class _CaptureAI:
    def __init__(self) -> None:
        self.requests = []

    async def triage_feedback(self, request) -> FeedbackTriageResponse:
        self.requests.append(request)
        return FeedbackTriageResponse(
            report_id=request.report_id,
            verdict=TriageVerdict.NEEDS_APPROVAL,
            labels=["ai:needs-approval"],
            model="gpt-4o-mini",
            ladder_level=None,
        )


class _DisabledGitHub:
    enabled = False


async def test_runner_passes_and_records_the_ceiling(db_session: AsyncSession) -> None:
    await _seed(db_session, VERIFIED=20)  # earned M
    pending = _report("TRACKED", 999)
    db_session.add(pending)
    await db_session.commit()

    ai = _CaptureAI()
    await feedback_triage_runner.triage_pending(db_session, ai, _DisabledGitHub())

    assert len(ai.requests) == 1
    assert ai.requests[0].max_auto_effort == "M"

    row = (
        await db_session.execute(select(FeedbackReport).where(FeedbackReport.id == pending.id))
    ).scalar_one()
    assert row.triage["max_auto_effort"] == "M"
    assert row.triage["ladder_level"] is None  # non-auto verdict carries no rung
