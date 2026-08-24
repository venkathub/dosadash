"""Feedback triage runner + admin triage-now (Phase 13 slice 3)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport, StaffAction, User
from dosadash_api.main import app
from dosadash_api.services import feedback_triage_runner
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_api.services.github_client import GitHubError, get_github_client
from dosadash_shared import (
    FeedbackTriageResponse,
    Role,
    TriageAssessment,
    TriageVerdict,
)


class FakeAI:
    def __init__(self, verdict: TriageVerdict | None = TriageVerdict.AUTO_FIX) -> None:
        self.verdict = verdict
        self.calls: list[int] = []

    async def triage_feedback(self, request) -> FeedbackTriageResponse:
        self.calls.append(request.report_id)
        if self.verdict is None:
            raise AIServiceError("ai unreachable")
        labels = {
            TriageVerdict.AUTO_FIX: ["ai:auto-fix"],
            TriageVerdict.NEEDS_APPROVAL: ["ai:needs-approval"],
            TriageVerdict.DISMISS: [],
        }[self.verdict]
        return FeedbackTriageResponse(
            report_id=request.report_id,
            verdict=self.verdict,
            assessment=TriageAssessment(
                actionable=self.verdict != TriageVerdict.DISMISS,
                type="BUG",
                severity="LOW",
                effort="S",
                risk="LOW",
                summary="fake",
            ),
            labels=labels,
            model="gpt-4o-mini",
        )


class FakeGitHub:
    def __init__(self, *, enabled: bool = True, fail: bool = False) -> None:
        self._enabled = enabled
        self._fail = fail
        self.labeled: list[tuple[int, list[str]]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        if self._fail:
            raise GitHubError("boom")
        self.labeled.append((issue_number, labels))


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="CUSTOMER",
        type="BUG",
        status="TRACKED",
        title="Typo on checkout",
        description="Procede instead of Proceed",
        dedupe_hash="d" * 64,
        github_issue_number=41,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def test_auto_fix_verdict_persisted_and_labeled(db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()
    ai, github = FakeAI(), FakeGitHub()

    summary = await feedback_triage_runner.triage_pending(db_session, ai, github)

    assert summary == {"examined": 1, "triaged": 1, "skipped": 0, "label_failures": 0}
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "AUTO_FIX"
    assert row.triage["verdict"] == "AUTO_FIX"
    assert row.triage["model"] == "gpt-4o-mini"
    assert row.triage["prompt_version"] == "feedback_triage_v1"
    assert github.labeled == [(41, ["ai:auto-fix"])]


async def test_ai_unreachable_leaves_report_for_next_run(db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()

    summary = await feedback_triage_runner.triage_pending(db_session, FakeAI(None), FakeGitHub())

    assert summary["skipped"] == 1 and summary["triaged"] == 0
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "TRACKED" and row.triage is None  # untouched → retried


async def test_dismiss_applies_no_labels(db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()
    github = FakeGitHub()

    await feedback_triage_runner.triage_pending(db_session, FakeAI(TriageVerdict.DISMISS), github)

    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "DISMISSED"
    assert github.labeled == []


async def test_label_failure_keeps_local_verdict(db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()

    summary = await feedback_triage_runner.triage_pending(
        db_session, FakeAI(), FakeGitHub(fail=True)
    )

    assert summary["label_failures"] == 1 and summary["triaged"] == 1
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "AUTO_FIX"  # verdict stands
    assert "label apply failed" in row.github_error


async def test_already_triaged_and_unmirrored_rows(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            _report(triage={"verdict": "AUTO_FIX"}, status="AUTO_FIX"),  # done — skip
            _report(dedupe_hash="e" * 64, github_issue_number=None, status="RECEIVED"),
        ]
    )
    await db_session.commit()
    ai, github = FakeAI(), FakeGitHub()

    summary = await feedback_triage_runner.triage_pending(db_session, ai, github)

    assert summary["examined"] == 1  # only the untriaged row
    assert len(ai.calls) == 1
    assert github.labeled == []  # no issue number → no label call
    rows = (await db_session.execute(select(FeedbackReport).order_by(FeedbackReport.id))).scalars()
    assert [r.status for r in rows] == ["AUTO_FIX", "AUTO_FIX"]


# ---------------------------------------------------------------- admin endpoint


async def _admin(client, db_session: AsyncSession, phone="9111177903") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    user = (await db_session.execute(select(User).where(User.phone.contains(phone)))).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def test_triage_now_endpoint_runs_and_audits(client, db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()
    headers = await _admin(client, db_session)
    fake_ai, fake_github = FakeAI(), FakeGitHub()
    app.dependency_overrides[get_ai_client] = lambda: fake_ai
    app.dependency_overrides[get_github_client] = lambda: fake_github
    try:
        resp = await client.post("/api/v1/admin/feedback/triage-now", headers=headers)
    finally:
        app.dependency_overrides.pop(get_ai_client, None)
        app.dependency_overrides.pop(get_github_client, None)
    assert resp.status_code == 200
    assert resp.json()["triaged"] == 1
    action = (
        await db_session.execute(
            select(StaffAction).where(StaffAction.action == "feedback.triage_now")
        )
    ).scalar_one()
    assert action.detail["triaged"] == 1


async def test_triage_now_requires_admin(client) -> None:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9111177904"})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": "9111177904", "otp": otp})
    headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}
    resp = await client.post("/api/v1/admin/feedback/triage-now", headers=headers)
    assert resp.status_code == 403
