"""Earned-autonomy computation (Phase 15 S6, docs/15) — the capability
ladder's AUTO_FIX_M unlock.

The ladder's M rung is EARNED from measured loop outcomes, never
configured: M-effort bugs may auto-fix only once the loop has proven
itself on S-effort fixes — ≥ AUTO_FIX_M_MIN_MERGED_FIXES merged AND a
concluded-verification rate ≥ AUTO_FIX_M_MIN_VERIFICATION_RATE.

Definitions (dish-QC honesty):
- merged_fixes    = reports whose CURRENT status is FIXED, VERIFIED, or
                    REOPENED (a reopened fix still merged — it just
                    failed verification, which the rate punishes).
- verification_rate = VERIFIED / (VERIFIED + REOPENED) over CONCLUDED
                    verifications only. FIXED-but-not-yet-verified rows
                    are pending, not evidence either way — counting them
                    in the denominator would punish a slow verifier, not
                    a bad fixer. No concluded samples → None → locked.

`unlocked()` is pure and unit-tested; `compute()` reads counts all-time
(autonomy is a trust level, not a windowed stat — one good week must not
unlock what a bad quarter earned back)."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport
from dosadash_shared import (
    AUTO_FIX_M_MIN_MERGED_FIXES,
    AUTO_FIX_M_MIN_VERIFICATION_RATE,
    FeedbackStatus,
)

_MERGED_STATUSES = (
    FeedbackStatus.FIXED.value,
    FeedbackStatus.VERIFIED.value,
    FeedbackStatus.REOPENED.value,
)


def unlocked(merged_fixes: int, verification_rate: float | None) -> bool:
    """Pure unlock rule — the only writer of the M ceiling."""
    return (
        merged_fixes >= AUTO_FIX_M_MIN_MERGED_FIXES
        and verification_rate is not None
        and verification_rate >= AUTO_FIX_M_MIN_VERIFICATION_RATE
    )


async def compute(session: AsyncSession) -> dict[str, Any]:
    """All-time autonomy state for the triage runner + metrics endpoint."""
    counts: dict[str, int] = {status: 0 for status in _MERGED_STATUSES}
    rows = await session.execute(
        select(FeedbackReport.status, func.count(FeedbackReport.id))
        .where(FeedbackReport.status.in_(_MERGED_STATUSES))
        .group_by(FeedbackReport.status)
    )
    for status, count in rows:
        counts[status] = count

    merged_fixes = sum(counts.values())
    concluded = counts[FeedbackStatus.VERIFIED.value] + counts[FeedbackStatus.REOPENED.value]
    verification_rate = (
        round(counts[FeedbackStatus.VERIFIED.value] / concluded, 4) if concluded else None
    )
    is_unlocked = unlocked(merged_fixes, verification_rate)
    return {
        "max_auto_effort": "M" if is_unlocked else "S",
        "merged_fixes": merged_fixes,
        "verification_rate": verification_rate,
        "min_merged_fixes": AUTO_FIX_M_MIN_MERGED_FIXES,
        "min_verification_rate": AUTO_FIX_M_MIN_VERIFICATION_RATE,
    }
