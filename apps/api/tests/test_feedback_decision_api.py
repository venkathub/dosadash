"""Feedback decision flow (Phase 13 slice 4): Telegram-internal + web
fallback share one transition; approving flips the fixer-trigger label."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport, StaffAction, User
from dosadash_api.main import app
from dosadash_api.services import feedback_notify
from dosadash_api.services.github_client import get_github_client
from dosadash_shared import Role

DECISION = "/api/v1/internal/feedback/decision"


class FakeGitHub:
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self.added: list[tuple[int, list[str]]] = []
        self.removed: list[tuple[int, str]] = []
        self.comments: list[tuple[int, str]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self.added.append((issue_number, labels))

    async def remove_label(self, issue_number: int, label: str) -> None:
        self.removed.append((issue_number, label))

    async def comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))


@pytest.fixture
def fake_github():
    fake = FakeGitHub()
    app.dependency_overrides[get_github_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_github_client, None)


def _internal(monkeypatch) -> dict:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    get_settings.cache_clear()
    return {"X-Internal-Token": "test-internal"}


async def _linked_user(db_session: AsyncSession, phone: str, role: Role, tg_user_id: int) -> User:
    user = User(phone=phone, name=f"{role.value} user", role=role, tg_user_id=tg_user_id)
    db_session.add(user)
    await db_session.commit()
    return user


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="CUSTOMER",
        type="BUG",
        status="NEEDS_APPROVAL",
        title="Order history slow",
        description="10s load",
        dedupe_hash="f" * 64,
        github_issue_number=55,
        triage={"assessment": {"summary": "needs pagination", "effort": "M", "risk": "LOW"}},
    )
    base.update(overrides)
    return FeedbackReport(**base)


# ---------------------------------------------------------------- internal (Telegram)


async def test_linked_admin_approves(client, db_session, fake_github, monkeypatch) -> None:
    headers = _internal(monkeypatch)
    await _linked_user(db_session, "+919555570001", Role.ADMIN, tg_user_id=888001)
    report = _report()
    db_session.add(report)
    await db_session.commit()

    resp = await client.post(
        DECISION,
        json={"tg_user_id": 888001, "report_id": report.id, "action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "APPROVED", "detail": None}

    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    await db_session.refresh(row)
    assert row.status == "APPROVED"
    assert fake_github.added == [(55, ["ai:approved"])]  # THE fixer trigger
    assert fake_github.removed == [(55, "ai:needs-approval")]
    assert "approved" in fake_github.comments[0][1]
    action = (
        await db_session.execute(
            select(StaffAction).where(StaffAction.action == "feedback.approve")
        )
    ).scalar_one()
    assert action.entity == f"feedback_report:{report.id}"


async def test_linked_customer_cannot_decide(client, db_session, fake_github, monkeypatch) -> None:
    headers = _internal(monkeypatch)
    await _linked_user(db_session, "+919555570002", Role.CUSTOMER, tg_user_id=888002)
    report = _report()
    db_session.add(report)
    await db_session.commit()

    resp = await client.post(
        DECISION,
        json={"tg_user_id": 888002, "report_id": report.id, "action": "approve"},
        headers=headers,
    )
    assert resp.status_code == 200  # soft-fail renders as card text
    assert resp.json()["ok"] is False
    assert fake_github.added == []
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "NEEDS_APPROVAL"


async def test_unknown_tg_account_and_missing_report(
    client, db_session, fake_github, monkeypatch
) -> None:
    headers = _internal(monkeypatch)
    resp = await client.post(
        DECISION, json={"tg_user_id": 1, "report_id": 1, "action": "reject"}, headers=headers
    )
    assert resp.json()["ok"] is False

    await _linked_user(db_session, "+919555570003", Role.OWNER, tg_user_id=888003)
    resp = await client.post(
        DECISION, json={"tg_user_id": 888003, "report_id": 999, "action": "reject"}, headers=headers
    )
    assert resp.json() == {"ok": False, "status": None, "detail": "Report not found."}


async def test_wrong_internal_token_403(client, fake_github, monkeypatch) -> None:
    _internal(monkeypatch)
    resp = await client.post(
        DECISION,
        json={"tg_user_id": 1, "report_id": 1, "action": "approve"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert resp.status_code == 403


async def test_only_needs_approval_is_decidable(
    client, db_session, fake_github, monkeypatch
) -> None:
    headers = _internal(monkeypatch)
    await _linked_user(db_session, "+919555570004", Role.ADMIN, tg_user_id=888004)
    report = _report(status="AUTO_FIX")  # already dispatched — not decidable
    db_session.add(report)
    await db_session.commit()

    resp = await client.post(
        DECISION,
        json={"tg_user_id": 888004, "report_id": report.id, "action": "reject"},
        headers=headers,
    )
    assert resp.json()["ok"] is False
    assert "not NEEDS_APPROVAL" in resp.json()["detail"]


# ---------------------------------------------------------------- web fallback


async def _admin_headers(client, db_session, phone="9111177905") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    user = (await db_session.execute(select(User).where(User.phone.contains(phone)))).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def test_web_reject_flow(client, db_session, fake_github) -> None:
    headers = await _admin_headers(client, db_session)
    report = _report()
    db_session.add(report)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/admin/feedback/{report.id}/decision",
        json={"action": "reject"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"
    assert fake_github.added == [(55, ["ai:rejected"])]

    # deciding twice → 409 (state machine, not idempotent overwrite)
    again = await client.post(
        f"/api/v1/admin/feedback/{report.id}/decision",
        json={"action": "approve"},
        headers=headers,
    )
    assert again.status_code == 409


async def test_web_decision_requires_admin(client, fake_github) -> None:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9111177906"})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": "9111177906", "otp": otp})
    resp = await client.post(
        "/api/v1/admin/feedback/1/decision",
        json={"action": "approve"},
        headers={"Authorization": f"Bearer {verify.json()['access_token']}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------- notify service


async def test_notify_unconfigured_is_silent_noop(db_session, monkeypatch) -> None:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "")
    get_settings.cache_clear()
    report = _report()
    db_session.add(report)
    await db_session.commit()
    assert await feedback_notify.notify_admins_feedback(db_session, report) == 0


async def test_notify_no_linked_admins_returns_zero(db_session, monkeypatch) -> None:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    get_settings.cache_clear()
    report = _report()
    db_session.add(report)
    await db_session.commit()
    # no ADMIN/OWNER with tg_user_id linked → 0 without any HTTP call
    assert await feedback_notify.notify_admins_feedback(db_session, report) == 0
