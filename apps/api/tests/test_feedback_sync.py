"""Reconciler (Phase 14 slice 1): derive_status precedence + sync_github
drift correction — the webhook's safety net."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport
from dosadash_api.services import feedback_sync
from dosadash_api.services.feedback_sync import derive_status
from dosadash_api.services.github_client import GitHubError
from dosadash_shared import FeedbackStatus as S

# ------------------------------------------------------------ derive_status


def test_verified_label_wins_over_everything() -> None:
    assert (
        derive_status(
            S.FIXED,
            labels=["ai:fixed", "ai:verified"],
            issue_state="closed",
            state_reason="completed",
            pr={"number": 7, "state": "closed", "merged_at": "2026-08-25T00:00:00Z"},
        )
        == S.VERIFIED
    )


def test_merged_pr_outranks_stale_labels() -> None:
    # ai:fixed never landed (workflow died post-merge) — the PR is truth.
    assert (
        derive_status(
            S.FIXING,
            labels=["ai:approved"],
            issue_state="open",
            state_reason=None,
            pr={"number": 7, "state": "closed", "merged_at": "2026-08-25T00:00:00Z"},
        )
        == S.FIXED
    )


def test_open_pr_upgrades_approved() -> None:
    assert (
        derive_status(
            S.APPROVED,
            labels=["ai:approved"],
            issue_state="open",
            state_reason=None,
            pr={"number": 7, "state": "open", "merged_at": None},
        )
        == S.PR_OPEN
    )


def test_fixed_but_issue_reopened() -> None:
    assert (
        derive_status(S.FIXED, labels=["ai:fixed"], issue_state="open", state_reason=None, pr=None)
        == S.REOPENED
    )


def test_not_planned_close_dismisses() -> None:
    assert (
        derive_status(
            S.NEEDS_APPROVAL, labels=[], issue_state="closed", state_reason="not_planned", pr=None
        )
        == S.DISMISSED
    )


def test_tracked_adopts_lost_verdict_label() -> None:
    # triage label applied on GitHub but the local write was lost.
    assert (
        derive_status(
            S.TRACKED, labels=["ai:needs-approval"], issue_state="open", state_reason=None, pr=None
        )
        == S.NEEDS_APPROVAL
    )


def test_no_drift_no_change() -> None:
    assert (
        derive_status(
            S.NEEDS_APPROVAL,
            labels=["ai:needs-approval"],
            issue_state="open",
            state_reason=None,
            pr=None,
        )
        == S.NEEDS_APPROVAL
    )


# ------------------------------------------------------------- sync_github


class FakeGitHub:
    def __init__(self, issues: dict[int, dict], prs: dict[int, dict | None] | None = None) -> None:
        self._issues = issues
        self._prs = prs or {}
        self.enabled = True

    async def get_issue(self, issue_number: int) -> dict:
        issue = self._issues.get(issue_number)
        if issue is None:
            raise GitHubError("issue get failed: HTTP 404")
        return issue

    async def find_fix_pr(self, issue_number: int) -> dict | None:
        return self._prs.get(issue_number)


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="FIXING",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="b" * 64,
        github_issue_number=120,
    )
    base.update(overrides)
    return FeedbackReport(**base)


def _issue(labels: list[str], state: str = "open", state_reason: str | None = None) -> dict:
    return {"state": state, "state_reason": state_reason, "labels": labels, "closed_at": None}


async def test_sync_corrects_drift_with_synced_event(db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    github = FakeGitHub(
        issues={120: _issue(["ai:approved", "ai:fixed"])},
        prs={120: {"number": 9, "state": "closed", "merged_at": "2026-08-25T00:00:00Z"}},
    )
    summary = await feedback_sync.sync_github(db_session, github)
    assert summary == {"examined": 1, "corrected": 1, "skipped": 0}
    await db_session.refresh(report)
    assert report.status == "FIXED"
    assert report.fix_pr_number == 9
    event = (
        await db_session.execute(select(FeedbackEvent).where(FeedbackEvent.report_id == report.id))
    ).scalar_one()
    assert event.stage == "SYNCED"
    assert event.actor == "reconciler"
    assert event.payload["from"] == "FIXING" and event.payload["to"] == "FIXED"


async def test_sync_in_agreement_touches_nothing(db_session: AsyncSession) -> None:
    report = _report(status="NEEDS_APPROVAL")
    db_session.add(report)
    await db_session.commit()
    github = FakeGitHub(issues={120: _issue(["ai:needs-approval"])})
    summary = await feedback_sync.sync_github(db_session, github)
    assert summary == {"examined": 1, "corrected": 0, "skipped": 0}
    events = (await db_session.execute(select(FeedbackEvent))).scalars().all()
    assert events == []


async def test_sync_verified_stamps_timestamp(db_session: AsyncSession) -> None:
    report = _report(status="FIXED")
    db_session.add(report)
    await db_session.commit()
    github = FakeGitHub(issues={120: _issue(["ai:fixed", "ai:verified"], state="closed")})
    await feedback_sync.sync_github(db_session, github)
    await db_session.refresh(report)
    assert report.status == "VERIFIED"
    assert report.verified_at is not None


async def test_sync_github_error_skips_report(db_session: AsyncSession) -> None:
    db_session.add(_report(github_issue_number=999, status="FIXING"))
    await db_session.commit()
    github = FakeGitHub(issues={})  # 404s everything
    summary = await feedback_sync.sync_github(db_session, github)
    assert summary == {"examined": 1, "corrected": 0, "skipped": 1}


async def test_sync_disabled_github_noops(db_session: AsyncSession) -> None:
    github = FakeGitHub(issues={})
    github.enabled = False
    summary = await feedback_sync.sync_github(db_session, github)
    assert summary["disabled"] is True


async def test_terminal_statuses_not_examined(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            _report(status="VERIFIED", dedupe_hash="c" * 64, github_issue_number=121),
            _report(status="REJECTED", dedupe_hash="d" * 64, github_issue_number=122),
            _report(status="DISMISSED", dedupe_hash="e" * 64, github_issue_number=123),
        ]
    )
    await db_session.commit()
    github = FakeGitHub(issues={})
    summary = await feedback_sync.sync_github(db_session, github)
    assert summary == {"examined": 0, "corrected": 0, "skipped": 0}
