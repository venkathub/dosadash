"""Fixer dispatch watchdog (post-Phase-14, Actions-outage postmortem):
pure classify/decide policies, the watch orchestrator, stall dedupe,
auto-resume label re-dispatch, and the /admin/feedback/ops endpoint."""

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackEvent, FeedbackReport
from dosadash_api.services import fixer_watchdog
from dosadash_api.services.fixer_watchdog import classify, decide
from dosadash_shared import FIXER_WORKFLOW_FILE, FeedbackEventStage

NOW = datetime(2026, 8, 26, 16, 0, 0)
DISPATCHED_OLD = NOW - timedelta(minutes=30)
DISPATCHED_FRESH = NOW - timedelta(minutes=2)


def _run(
    run_id: int = 1,
    status: str = "queued",
    conclusion: str | None = None,
    minutes_ago: int = 25,
) -> dict:
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "display_title": "[user-bug] Icon display UI/UX issue",
        "event": "issues",
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat() + "Z",
    }


# ---------------------------------------------------------------- classify


def test_in_progress_run_is_healthy() -> None:
    verdict, _ = classify([_run(status="in_progress")], dispatched_at=DISPATCHED_OLD, now=NOW)
    assert verdict == "RUNNING"


def test_fresh_dispatch_waits() -> None:
    verdict, _ = classify([], dispatched_at=DISPATCHED_FRESH, now=NOW)
    assert verdict == "WAITING"


def test_young_queued_run_waits() -> None:
    verdict, _ = classify(
        [_run(status="queued", minutes_ago=3)],
        dispatched_at=NOW - timedelta(minutes=4),
        now=NOW,
    )
    assert verdict == "WAITING"


def test_stuck_queued_run_stalls() -> None:
    verdict, evidence = classify(
        [_run(run_id=42, status="queued", minutes_ago=25)],
        dispatched_at=DISPATCHED_OLD,
        now=NOW,
    )
    assert verdict == "STALLED"
    assert evidence["reason"] == "run_queued"
    assert evidence["run_id"] == 42
    assert evidence["queued_minutes"] >= 10


def test_startup_failure_run_stalls_as_run_died() -> None:
    verdict, evidence = classify(
        [_run(run_id=7, status="completed", conclusion="startup_failure")],
        dispatched_at=DISPATCHED_OLD,
        now=NOW,
    )
    assert verdict == "STALLED"
    assert evidence == {"reason": "run_died", "run_id": 7, "conclusion": "startup_failure"}


def test_no_run_at_all_is_dispatch_lost() -> None:
    verdict, evidence = classify([], dispatched_at=DISPATCHED_OLD, now=NOW)
    assert verdict == "STALLED"
    assert evidence["reason"] == "dispatch_lost"


def test_successful_run_settles() -> None:
    # success or genuine failure: run ingest / webhooks own the outcome.
    for conclusion in ("success", "failure"):
        verdict, _ = classify(
            [_run(status="completed", conclusion=conclusion)],
            dispatched_at=DISPATCHED_OLD,
            now=NOW,
        )
        assert verdict == "SETTLED"


def test_runs_before_dispatch_window_are_ignored() -> None:
    # an old success from a PREVIOUS report never masks this stall.
    old_success = _run(status="completed", conclusion="success", minutes_ago=200)
    verdict, evidence = classify([old_success], dispatched_at=DISPATCHED_OLD, now=NOW)
    assert verdict == "STALLED"
    assert evidence["reason"] == "dispatch_lost"


# ------------------------------------------------------------------ decide


def test_not_stalled_is_no_action() -> None:
    for verdict in ("RUNNING", "WAITING", "SETTLED"):
        assert decide(verdict, {}, gh_operational=True, retries=0) == "NONE"


def test_outage_blocks_redispatch_but_records() -> None:
    action = decide("STALLED", {"reason": "run_died"}, gh_operational=False, retries=0)
    assert action == "RECORD_STALL"


def test_unknown_health_never_freezes_recovery() -> None:
    # githubstatus.com itself down → treat as operational (the beat retries
    # anyway; freezing on unknown would make two outages block each other).
    action = decide("STALLED", {"reason": "run_died"}, gh_operational=None, retries=0)
    assert action == "REDISPATCH"


def test_queued_stall_cancels_before_redispatch() -> None:
    action = decide(
        "STALLED", {"reason": "run_queued", "run_id": 42}, gh_operational=True, retries=1
    )
    assert action == "CANCEL_AND_REDISPATCH"


def test_retries_cap_is_terminal() -> None:
    action = decide("STALLED", {"reason": "run_died"}, gh_operational=True, retries=3)
    assert action == "RECORD_STALL"


# ------------------------------------------------------------------- watch


def _live_run(
    run_id: int = 1,
    status: str = "queued",
    conclusion: str | None = None,
    minutes_ago: int = 25,
) -> dict:
    """Run stamped relative to the REAL clock — watch() uses datetime.now."""
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "display_title": "[user-bug] Icon display UI/UX issue",
        "event": "issues",
        "created_at": (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat() + "Z",
    }


class FakeGitHub:
    def __init__(
        self,
        runs: list[dict] | None = None,
        labels: list[str] | None = None,
        cancel_ok: bool = True,
    ) -> None:
        self.enabled = True
        self.runs = runs or []
        self.labels = labels if labels is not None else ["bug", "ai:approved"]
        self.cancel_ok = cancel_ok
        self.cancelled: list[int] = []
        self.removed: list[str] = []
        self.added: list[list[str]] = []
        self.list_calls = 0

    async def list_workflow_runs(self, workflow_file: str, *, per_page: int = 30) -> list[dict]:
        assert workflow_file == FIXER_WORKFLOW_FILE
        self.list_calls += 1
        return self.runs

    async def cancel_workflow_run(self, run_id: int) -> bool:
        self.cancelled.append(run_id)
        return self.cancel_ok

    async def get_issue(self, issue_number: int) -> dict:
        return {"state": "open", "state_reason": None, "labels": self.labels, "closed_at": None}

    async def remove_label(self, issue_number: int, label: str) -> None:
        self.removed.append(label)

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self.added.append(labels)


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="FIXING",
        title="Icon display UI/UX issue",
        description="High Protein icon shows with no data",
        dedupe_hash="c" * 64,
        github_issue_number=138,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def _dispatch_event(
    session: AsyncSession, report: FeedbackReport, minutes_ago: int = 30
) -> None:
    session.add(
        FeedbackEvent(
            report_id=report.id,
            stage=FeedbackEventStage.FIX_STARTED.value,
            actor="webhook:github",
            payload={"label": "ai:approved"},
        )
    )
    await session.commit()
    # backdate (created_at server default is now())
    event = (
        (await session.execute(select(FeedbackEvent).where(FeedbackEvent.report_id == report.id)))
        .scalars()
        .all()[-1]
    )
    event.created_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    await session.commit()


def _operational(monkeypatch) -> None:
    async def fake_status(*, force: bool = False):
        return {"status": "operational", "incident": None, "checked_at": "x"}

    monkeypatch.setattr(fixer_watchdog, "fetch_actions_status", fake_status)


def _outage(monkeypatch) -> None:
    async def fake_status(*, force: bool = False):
        return {"status": "major_outage", "incident": "Incident with Actions", "checked_at": "x"}

    monkeypatch.setattr(fixer_watchdog, "fetch_actions_status", fake_status)


async def test_dead_run_redispatches_the_trigger_label(
    db_session: AsyncSession, monkeypatch
) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    await _dispatch_event(db_session, report)
    _operational(monkeypatch)
    github = FakeGitHub(
        runs=[_live_run(run_id=9, status="completed", conclusion="startup_failure")]
    )
    summary = await fixer_watchdog.watch(db_session, github)
    assert summary["retried"] == 1 and summary["stalled"] == 0
    assert github.removed == ["ai:approved"]
    assert github.added == [["ai:approved"]]
    events = (
        (
            await db_session.execute(
                select(FeedbackEvent).where(
                    FeedbackEvent.report_id == report.id,
                    FeedbackEvent.stage == FeedbackEventStage.FIX_RETRIED.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].actor == "watchdog"
    assert events[0].payload["attempt"] == 1
    assert events[0].payload["reason"] == "run_died"


async def test_outage_records_stall_once_never_redispatches(
    db_session: AsyncSession, monkeypatch
) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    await _dispatch_event(db_session, report)
    _outage(monkeypatch)
    github = FakeGitHub(runs=[_live_run(run_id=9, status="queued", minutes_ago=25)])
    first = await fixer_watchdog.watch(db_session, github)
    second = await fixer_watchdog.watch(db_session, github)
    assert first["stalled"] == 1
    assert second["stalled"] == 0  # deduped — one ping per stall, not per beat
    assert github.removed == [] and github.added == []
    stalls = (
        (
            await db_session.execute(
                select(FeedbackEvent).where(
                    FeedbackEvent.stage == FeedbackEventStage.FIX_STALLED.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(stalls) == 1
    assert stalls[0].payload["reason"] == "run_queued"
    assert stalls[0].payload["github_actions"]["status"] == "major_outage"


async def test_queued_stall_cancels_then_redispatches(
    db_session: AsyncSession, monkeypatch
) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    await _dispatch_event(db_session, report)
    _operational(monkeypatch)
    github = FakeGitHub(runs=[_live_run(run_id=42, status="queued", minutes_ago=25)])
    summary = await fixer_watchdog.watch(db_session, github)
    assert summary["retried"] == 1
    assert github.cancelled == [42]
    assert github.added == [["ai:approved"]]


async def test_cancel_forbidden_degrades_to_stall(db_session: AsyncSession, monkeypatch) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    await _dispatch_event(db_session, report)
    _operational(monkeypatch)
    github = FakeGitHub(
        runs=[_live_run(run_id=42, status="queued", minutes_ago=25)], cancel_ok=False
    )
    summary = await fixer_watchdog.watch(db_session, github)
    assert summary["retried"] == 0 and summary["stalled"] == 1
    assert github.added == []
    stall = (
        await db_session.execute(
            select(FeedbackEvent).where(FeedbackEvent.stage == FeedbackEventStage.FIX_STALLED.value)
        )
    ).scalar_one()
    assert stall.payload["reason"] == "cancel_forbidden"


async def test_retries_exhausted_is_terminal_stall(db_session: AsyncSession, monkeypatch) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    await _dispatch_event(db_session, report)
    for attempt in (1, 2, 3):
        db_session.add(
            FeedbackEvent(
                report_id=report.id,
                stage=FeedbackEventStage.FIX_RETRIED.value,
                actor="watchdog",
                payload={"attempt": attempt},
            )
        )
    await db_session.commit()
    # backdate the retries — a retry re-arms the stall window, so the last
    # one must be old enough for the report to be stalled AGAIN.
    retries = (
        (
            await db_session.execute(
                select(FeedbackEvent).where(
                    FeedbackEvent.stage == FeedbackEventStage.FIX_RETRIED.value
                )
            )
        )
        .scalars()
        .all()
    )
    for event in retries:
        event.created_at = datetime.utcnow() - timedelta(minutes=20)
    await db_session.commit()
    _operational(monkeypatch)
    github = FakeGitHub(
        runs=[_live_run(run_id=9, status="completed", conclusion="startup_failure")]
    )
    summary = await fixer_watchdog.watch(db_session, github)
    assert summary["retried"] == 0 and summary["stalled"] == 1
    assert github.added == []
    stall = (
        await db_session.execute(
            select(FeedbackEvent).where(FeedbackEvent.stage == FeedbackEventStage.FIX_STALLED.value)
        )
    ).scalar_one()
    assert stall.payload["reason"] == "retries_exhausted"


async def test_no_dispatched_reports_means_no_github_calls(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    summary = await fixer_watchdog.watch(db_session, github)
    assert summary == {"examined": 0, "stalled": 0, "retried": 0, "skipped": 0}
    assert github.list_calls == 0


# --------------------------------------------------------- current_stalls


async def test_current_stalls_and_supersession(db_session: AsyncSession) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    db_session.add(
        FeedbackEvent(
            report_id=report.id,
            stage=FeedbackEventStage.FIX_STALLED.value,
            actor="watchdog",
            payload={"reason": "run_died", "retries": 1},
        )
    )
    await db_session.commit()
    stalls = await fixer_watchdog.current_stalls(db_session)
    assert len(stalls) == 1
    assert stalls[0]["report_id"] == report.id
    assert stalls[0]["reason"] == "run_died"
    # a later retry supersedes the stall
    db_session.add(
        FeedbackEvent(
            report_id=report.id,
            stage=FeedbackEventStage.FIX_RETRIED.value,
            actor="watchdog",
            payload={"attempt": 2},
        )
    )
    await db_session.commit()
    assert await fixer_watchdog.current_stalls(db_session) == []


# ------------------------------------------------------------ ops endpoint


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def test_ops_endpoint_reports_status_and_stalls(
    client, db_session: AsyncSession, monkeypatch
) -> None:
    from dosadash_api.db.models import User
    from dosadash_shared import Role

    headers = await _login(client, "9111177930")
    user = (
        await db_session.execute(select(User).where(User.phone.contains("9111177930")))
    ).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()

    report = _report()
    db_session.add(report)
    await db_session.commit()
    db_session.add(
        FeedbackEvent(
            report_id=report.id,
            stage=FeedbackEventStage.FIX_STALLED.value,
            actor="watchdog",
            payload={"reason": "run_queued", "run_id": 42, "retries": 1},
        )
    )
    await db_session.commit()
    _outage(monkeypatch)

    resp = await client.get("/api/v1/admin/feedback/ops", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["github_actions"]["status"] == "major_outage"
    assert body["github_actions"]["incident"] == "Incident with Actions"
    assert len(body["stalls"]) == 1
    assert body["stalls"][0]["report_id"] == report.id
    assert body["stalls"][0]["reason"] == "run_queued"
    assert body["stalls"][0]["run_id"] == 42


async def test_ops_endpoint_requires_admin(client) -> None:
    headers = await _login(client, "9111177931")
    resp = await client.get("/api/v1/admin/feedback/ops", headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------- gates


def test_fixer_workflow_file_constant_matches_repo() -> None:
    """FIXER_WORKFLOW_FILE must name the real workflow file — the watchdog
    lists THIS workflow's runs (registry↔workflow coherence, same
    discipline as the trigger-label gate)."""
    root = Path(__file__).resolve().parents[3]
    assert (root / ".github" / "workflows" / FIXER_WORKFLOW_FILE).is_file()


def test_dead_conclusions_cover_startup_failure() -> None:
    """The whole point of the watchdog: startup_failure runs (observed
    live 2026-08-26) must classify as dead, never as settled."""
    assert "startup_failure" in fixer_watchdog.DEAD_CONCLUSIONS
