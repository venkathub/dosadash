"""Feedback lifecycle events (Phase 14 slice 1): guarded status projection,
timeline writes from the existing pipeline (intake/triage/decision), and
the admin timeline endpoint."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport, User
from dosadash_api.main import app
from dosadash_api.services import feedback_events
from dosadash_api.services.github_client import get_github_client
from dosadash_shared import FeedbackEventStage as Stage
from dosadash_shared import FeedbackStatus, Role


class FakeGitHub:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.created: list[dict] = []
        self.labels: list[tuple[int, list[str]]] = []
        self.comments: list[tuple[int, str]] = []

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> int:
        self.created.append({"title": title})
        return 200 + len(self.created)

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self.labels.append((issue_number, labels))

    async def remove_label(self, issue_number: int, label: str) -> None:
        pass

    async def comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))


@pytest.fixture
def fake_github():
    fake = FakeGitHub()
    app.dependency_overrides[get_github_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_github_client, None)


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _admin(client, db_session: AsyncSession, phone="9111178801") -> dict:
    headers = await _login(client, phone)
    user = (await db_session.execute(select(User).where(User.phone.contains(phone)))).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()
    return headers


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="TRACKED",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="f" * 64,
        github_issue_number=101,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def _stages(db_session: AsyncSession, report_id: int) -> list[str]:
    rows = await db_session.execute(
        select(FeedbackEvent.stage)
        .where(FeedbackEvent.report_id == report_id)
        .order_by(FeedbackEvent.id)
    )
    return [s for (s,) in rows.all()]


# ------------------------------------------------------- guarded projection


async def test_legal_transition_projects(db_session: AsyncSession) -> None:
    report = _report(status="RECEIVED", github_issue_number=None)
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(db_session, report, Stage.TRACKED, actor="system")
    await db_session.commit()
    assert report.status == FeedbackStatus.TRACKED


async def test_illegal_transition_is_timeline_only(db_session: AsyncSession) -> None:
    report = _report(status="TRACKED")
    db_session.add(report)
    await db_session.flush()
    # VERIFIED straight from TRACKED is not a legal projection — the event
    # is still recorded (timeline never lies) but status must not move.
    feedback_events.record(db_session, report, Stage.VERIFIED, actor="webhook:github")
    await db_session.commit()
    assert report.status == "TRACKED"
    assert report.verified_at is None
    assert await _stages(db_session, report.id) == ["VERIFIED"]


async def test_reentrant_loop_reopened_can_fix_again(db_session: AsyncSession) -> None:
    report = _report(status="REOPENED")
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(db_session, report, Stage.FIX_STARTED, actor="webhook:github")
    await db_session.commit()
    assert report.status == FeedbackStatus.FIXING


# --------------------------------------------------- pipeline instrumentation


async def test_intake_writes_received_and_tracked(client, db_session, fake_github) -> None:
    resp = await client.post(
        "/api/v1/feedback",
        json={
            "type": "BUG",
            "title": "Cart total wrong after coupon",
            "description": "Applied WELCOME10 and the GST line went negative on checkout.",
        },
    )
    assert resp.status_code == 201
    report_id = resp.json()["id"]
    assert await _stages(db_session, report_id) == ["RECEIVED", "TRACKED"]


async def test_intake_mirror_disabled_writes_received_only(client, db_session) -> None:
    fake = FakeGitHub(enabled=False)
    app.dependency_overrides[get_github_client] = lambda: fake
    try:
        resp = await client.post(
            "/api/v1/feedback",
            json={
                "type": "BUG",
                "title": "Cart total wrong after coupon",
                "description": "Applied WELCOME10 and the GST line went negative on checkout.",
            },
        )
    finally:
        app.dependency_overrides.pop(get_github_client, None)
    assert resp.status_code == 201
    assert await _stages(db_session, resp.json()["id"]) == ["RECEIVED"]


async def test_decision_writes_event(client, db_session, fake_github) -> None:
    headers = await _admin(client, db_session)
    report = _report(status="NEEDS_APPROVAL")
    db_session.add(report)
    await db_session.commit()
    resp = await client.post(
        f"/api/v1/admin/feedback/{report.id}/decision",
        json={"action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 200
    stages = await _stages(db_session, report.id)
    assert stages == ["APPROVED"]
    event = (
        await db_session.execute(select(FeedbackEvent).where(FeedbackEvent.report_id == report.id))
    ).scalar_one()
    assert event.actor.startswith("admin:")


# ------------------------------------------------------------ timeline API


async def test_timeline_endpoint(client, db_session, fake_github) -> None:
    headers = await _admin(client, db_session)
    report = _report(status="RECEIVED", github_issue_number=None)
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(db_session, report, Stage.RECEIVED, actor="system")
    feedback_events.record(db_session, report, Stage.TRACKED, actor="system", payload={"issue": 5})
    await db_session.commit()

    resp = await client.get(f"/api/v1/admin/feedback/{report.id}/events", headers=headers)
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert [e["stage"] for e in events] == ["RECEIVED", "TRACKED"]
    assert events[1]["payload"] == {"issue": 5}

    missing = await client.get("/api/v1/admin/feedback/999999/events", headers=headers)
    assert missing.status_code == 404


async def test_timeline_requires_admin(client, db_session) -> None:
    headers = await _login(client, "9111178802")
    resp = await client.get("/api/v1/admin/feedback/1/events", headers=headers)
    assert resp.status_code == 403


async def test_cascade_delete_removes_events(db_session: AsyncSession) -> None:
    report = _report()
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(db_session, report, Stage.RECEIVED, actor="system")
    await db_session.commit()
    await db_session.delete(report)
    await db_session.commit()
    remaining = (await db_session.execute(select(FeedbackEvent))).scalars().all()
    assert remaining == []
