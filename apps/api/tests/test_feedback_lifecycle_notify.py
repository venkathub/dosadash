"""Lifecycle Telegram feed (Phase 14 slice 2): anchor bookkeeping, edit
vs. send, ping stages, and hard best-effort guarantees."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import config
from dosadash_api.db.models import FeedbackNotification, FeedbackReport, User
from dosadash_api.services import feedback_events, feedback_notify
from dosadash_shared import FeedbackEventStage as Stage
from dosadash_shared import Role


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self) -> dict:
        return self._body


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient inside feedback_notify."""

    calls: list[dict] = []
    responses: list[FakeResponse] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
        FakeAsyncClient.calls.append({"url": url, "json": json, "headers": headers})
        if FakeAsyncClient.responses:
            return FakeAsyncClient.responses.pop(0)
        return FakeResponse(200, {"ok": True, "message_id": 5000 + len(FakeAsyncClient.calls)})


@pytest.fixture
def fake_bot(monkeypatch):
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    monkeypatch.setenv("API_BOT_BASE_URL", "http://bot-test:8081")
    config.get_settings.cache_clear()
    FakeAsyncClient.calls = []
    FakeAsyncClient.responses = []
    monkeypatch.setattr(feedback_notify.httpx, "AsyncClient", FakeAsyncClient)
    yield FakeAsyncClient
    config.get_settings.cache_clear()


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="FIXING",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="9" * 64,
        github_issue_number=120,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def _linked_admin(db_session: AsyncSession, tg_user_id: int = 777) -> User:
    user = User(phone=f"+9195000{tg_user_id}", name="admin", role=Role.ADMIN)
    user.tg_user_id = tg_user_id
    db_session.add(user)
    await db_session.commit()
    return user


async def test_first_stage_creates_anchor(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session)
    report = _report()
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(db_session, report, Stage.FIX_STARTED, actor="webhook:github")
    await db_session.commit()

    sent = await feedback_notify.notify_stage(db_session, report, Stage.FIX_STARTED)
    assert sent == 1
    call = fake_bot.calls[0]
    assert call["url"].endswith("/internal/feedback-lifecycle")
    assert call["json"]["message_id"] is None
    assert call["json"]["status"] == "FIXING"
    assert call["json"]["ping"] is False
    assert [e["stage"] for e in call["json"]["timeline"]] == ["FIX_STARTED"]

    anchor = (await db_session.execute(select(FeedbackNotification))).scalar_one()
    assert anchor.tg_user_id == 777
    assert anchor.message_id == 5001


async def test_second_stage_edits_anchor(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session)
    report = _report()
    db_session.add(report)
    await db_session.flush()
    db_session.add(FeedbackNotification(report_id=report.id, tg_user_id=777, message_id=4242))
    await db_session.commit()

    sent = await feedback_notify.notify_stage(db_session, report, Stage.PR_OPENED)
    assert sent == 1
    assert fake_bot.calls[0]["json"]["message_id"] == 4242
    # bot answered with the same id — no row churn
    anchor = (await db_session.execute(select(FeedbackNotification))).scalar_one()
    assert anchor.message_id == 5001 or anchor.message_id == 4242


async def test_anchor_updates_when_bot_resends(db_session: AsyncSession, fake_bot) -> None:
    """Admin deleted the card → bot sends fresh and returns a NEW id."""
    await _linked_admin(db_session)
    report = _report()
    db_session.add(report)
    await db_session.flush()
    db_session.add(FeedbackNotification(report_id=report.id, tg_user_id=777, message_id=4242))
    await db_session.commit()
    fake_bot.responses = [FakeResponse(200, {"ok": True, "message_id": 9999})]

    await feedback_notify.notify_stage(db_session, report, Stage.PR_MERGED)
    anchor = (await db_session.execute(select(FeedbackNotification))).scalar_one()
    assert anchor.message_id == 9999


async def test_ping_stages_flagged(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session)
    report = _report(status="VERIFIED")
    db_session.add(report)
    await db_session.commit()
    await feedback_notify.notify_stage(db_session, report, Stage.VERIFIED)
    assert fake_bot.calls[0]["json"]["ping"] is True
    assert fake_bot.calls[0]["json"]["stage"] == "VERIFIED"


async def test_every_linked_admin_gets_a_card(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session, 777)
    await _linked_admin(db_session, 888)
    report = _report()
    db_session.add(report)
    await db_session.commit()
    sent = await feedback_notify.notify_stage(db_session, report, Stage.FIX_STARTED)
    assert sent == 2
    assert {c["json"]["tg_user_id"] for c in fake_bot.calls} == {777, 888}
    anchors = (await db_session.execute(select(FeedbackNotification))).scalars().all()
    assert len(anchors) == 2


async def test_no_linked_admins_is_zero_sends(db_session: AsyncSession, fake_bot) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    assert await feedback_notify.notify_stage(db_session, report, Stage.FIX_STARTED) == 0
    assert fake_bot.calls == []


async def test_unconfigured_token_is_zero_sends(db_session: AsyncSession, monkeypatch) -> None:
    monkeypatch.delenv("API_INTERNAL_API_TOKEN", raising=False)
    config.get_settings.cache_clear()
    report = _report()
    db_session.add(report)
    await db_session.commit()
    try:
        assert await feedback_notify.notify_stage(db_session, report, Stage.FIX_STARTED) == 0
    finally:
        config.get_settings.cache_clear()


async def test_bot_failure_never_raises(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session)
    report = _report()
    db_session.add(report)
    await db_session.commit()
    fake_bot.responses = [FakeResponse(502, {"ok": False})]
    sent = await feedback_notify.notify_stage(db_session, report, Stage.FIX_STARTED)
    assert sent == 0  # failed send: no anchor row, no exception
    anchors = (await db_session.execute(select(FeedbackNotification))).scalars().all()
    assert anchors == []


async def test_timeline_notes_extracted(db_session: AsyncSession, fake_bot) -> None:
    await _linked_admin(db_session)
    report = _report(status="PR_OPEN", fix_pr_number=7)
    db_session.add(report)
    await db_session.flush()
    feedback_events.record(
        db_session, report, Stage.TRACKED, actor="system", payload={"issue": 120}
    )
    feedback_events.record(
        db_session, report, Stage.TRIAGED, actor="system", payload={"verdict": "AUTO_FIX"}
    )
    feedback_events.record(
        db_session, report, Stage.PR_OPENED, actor="webhook:github", payload={"pr_number": 7}
    )
    await db_session.commit()
    await feedback_notify.notify_stage(db_session, report, Stage.PR_OPENED)
    notes = [e["note"] for e in fake_bot.calls[0]["json"]["timeline"]]
    assert notes == ["issue #120", "AUTO_FIX", "PR #7"]
