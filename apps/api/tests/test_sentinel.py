"""Production sentinel tests (Phase 15 slice 1, docs/15 §S1).

Pure classifier tests (no I/O) + db-backed filing tests through the real
FeedbackReport path + the 5xx counter middleware. The scan()'s collectors
are injectable, so no network is ever probed here.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import EvalRun, FeedbackEvent, FeedbackReport
from dosadash_api.services import sentinel
from dosadash_api.services.sentinel import (
    ANOMALY_5XX_BURST,
    ANOMALY_EVAL_GATE,
    ANOMALY_SERVICE_DOWN,
    Anomaly,
    classify_5xx,
    classify_eval_runs,
    classify_health,
)
from dosadash_shared import (
    UNTRUSTED_BEGIN,
    UNTRUSTED_END,
    FeedbackStatus,
    FeedbackType,
    ReporterTier,
)

# ------------------------------------------------------------- classifiers


def test_classify_health_flags_only_failures() -> None:
    probes = {
        "api": {"ok": True, "status": 200, "error": None, "url": "http://api:8000/healthz"},
        "ai": {"ok": False, "status": 503, "error": None, "url": "http://ai:8001/healthz"},
        "bot": {"ok": False, "status": None, "error": "timeout", "url": "http://bot:8081/healthz"},
    }
    anomalies = classify_health(probes)
    assert [a.subject for a in anomalies] == ["ai", "bot"]
    assert all(a.kind == ANOMALY_SERVICE_DOWN for a in anomalies)
    assert anomalies[0].fingerprint == "service_down:ai"


def test_classify_5xx_respects_threshold() -> None:
    assert classify_5xx({1: 2, 2: 2}, threshold=5, window_minutes=15) is None
    burst = classify_5xx({1: 3, 2: 2}, threshold=5, window_minutes=15)
    assert burst is not None
    assert burst.kind == ANOMALY_5XX_BURST
    assert burst.evidence["total_5xx"] == 5


def test_classify_eval_runs_needs_consecutive_reds() -> None:
    """One red run is the documented flaky-first case — never an alert."""
    red = {"id": 2, "git_sha": "abc123def456", "gates_passed": False, "order_accuracy": 0.93}
    green = {"id": 1, "git_sha": "abc123def456", "gates_passed": True, "order_accuracy": 0.97}
    assert classify_eval_runs([]) is None
    assert classify_eval_runs([red]) is None
    assert classify_eval_runs([red, green]) is None
    assert classify_eval_runs([green, red]) is None
    anomaly = classify_eval_runs([red, dict(red, id=1)])
    assert anomaly is not None
    assert anomaly.kind == ANOMALY_EVAL_GATE
    assert anomaly.subject == "abc123def456"[:12]


def test_titles_and_fingerprints_are_stable_across_recurrences() -> None:
    """Dedupe rides `type|title|fingerprint` — volatile counts must never
    leak into either, or recurrences stop collapsing."""
    a = classify_5xx({1: 9}, threshold=5, window_minutes=15)
    b = classify_5xx({7: 55, 8: 12}, threshold=5, window_minutes=15)
    assert a.title == b.title  # "5xx" is fine; per-scan counts are not
    assert a.fingerprint == b.fingerprint
    assert str(a.evidence["total_5xx"]) not in a.title


def test_description_is_redacted_and_capped() -> None:
    anomaly = Anomaly(
        kind=ANOMALY_SERVICE_DOWN,
        subject="bot",
        title="bot service failing healthcheck",
        evidence={"error": "user +91 98765 43210 saw this " + "x" * 3000},
    )
    description = sentinel.build_description(anomaly, checked_at=datetime(2026, 8, 26, 12, 0))
    assert "98765" not in description  # Rule 8 — evidence echoes payloads
    assert len(description) <= 2000  # human-intake cap: downstream unsurprised


# ------------------------------------------------------------------ filing


class FakeGitHub:
    def __init__(self, *, enabled: bool = True, fail: bool = False) -> None:
        self._enabled = enabled
        self._fail = fail
        self.created: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> int:
        if self._fail:
            from dosadash_api.services.github_client import GitHubError

            raise GitHubError("boom")
        self.created.append({"title": title, "body": body, "labels": labels})
        return 700 + len(self.created)


def _anomaly() -> Anomaly:
    return Anomaly(
        kind=ANOMALY_SERVICE_DOWN,
        subject="ai",
        title="ai service failing healthcheck",
        evidence={"status": None, "error": "connect timeout"},
    )


async def test_file_anomalies_creates_system_report(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    result = await sentinel.file_anomalies(
        db_session, github, [_anomaly()], env="test", max_per_day=5
    )
    assert result["filed"] == 1

    report = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert report.reporter_tier == ReporterTier.SYSTEM
    assert report.type == FeedbackType.BUG
    assert report.user_id is None
    assert report.status == FeedbackStatus.TRACKED
    assert report.context["fingerprint"] == "service_down:ai"

    issue = github.created[0]
    assert issue["title"].startswith("[sentinel] ")
    assert issue["labels"] == ["sentinel", "bug"]
    # evidence is fenced — log strings are attacker-influencable
    assert UNTRUSTED_BEGIN in issue["body"] and UNTRUSTED_END in issue["body"]
    assert "connect timeout" in issue["body"]

    stages = [e.stage for e in (await db_session.execute(select(FeedbackEvent))).scalars()]
    assert stages == ["RECEIVED", "TRACKED"]


async def test_recurring_anomaly_collapses_onto_open_report(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    await sentinel.file_anomalies(db_session, github, [_anomaly()], env="test", max_per_day=5)
    # second pass: evidence differs (volatile), fingerprint identical
    again = Anomaly(
        kind=ANOMALY_SERVICE_DOWN,
        subject="ai",
        title="ai service failing healthcheck",
        evidence={"status": 503, "error": "different snapshot"},
    )
    result = await sentinel.file_anomalies(db_session, github, [again], env="test", max_per_day=5)
    assert result["filed"] == 0
    assert result["skipped_open"] == 1
    assert len(github.created) == 1  # no second GitHub issue


async def test_daily_cap_limits_refiling_after_dismissal(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    for _ in range(3):
        await sentinel.file_anomalies(db_session, github, [_anomaly()], env="test", max_per_day=3)
        # close it so the open-twin dedupe doesn't mask the cap
        report = (
            await db_session.execute(
                select(FeedbackReport).order_by(FeedbackReport.id.desc()).limit(1)
            )
        ).scalar_one()
        report.status = FeedbackStatus.DISMISSED
        await db_session.commit()
    result = await sentinel.file_anomalies(
        db_session, github, [_anomaly()], env="test", max_per_day=3
    )
    assert result["filed"] == 0
    assert result["skipped_capped"] == 1


async def test_github_outage_degrades_to_store_only(db_session: AsyncSession) -> None:
    result = await sentinel.file_anomalies(
        db_session, FakeGitHub(fail=True), [_anomaly()], env="test", max_per_day=5
    )
    assert result["filed"] == 1
    assert result["mirror_failures"] == 1
    report = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert report.status == FeedbackStatus.RECEIVED
    assert "boom" in report.github_error


async def test_scan_with_injected_signals(db_session: AsyncSession) -> None:
    """End-to-end pass with every collector injected: healthy fleet, one
    burst, no eval signal → exactly one SYSTEM report."""
    result = await sentinel.scan(
        db_session,
        FakeGitHub(),
        settings=get_settings(),
        probes={"api": {"ok": True, "status": 200, "error": None, "url": "u"}},
        counts={1: 99},
        eval_runs=[],
    )
    assert result["anomalies"] == 1
    assert result["filed"] == 1
    report = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert report.context["detector"] == ANOMALY_5XX_BURST


async def test_scan_reads_eval_runs_from_db(db_session: AsyncSession) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for i, offset in enumerate((2, 1)):
        db_session.add(
            EvalRun(
                ran_at=now - timedelta(hours=offset),
                git_sha="deadbeef" + str(i),
                trigger="ci",
                cases=174,
                order_accuracy=0.90,
                tool_correctness=1.0,
                gates_passed=False,
                failures=[],
                case_reports=[],
            )
        )
    await db_session.commit()
    result = await sentinel.scan(
        db_session,
        FakeGitHub(),
        settings=get_settings(),
        probes={},
        counts={},
    )
    assert result["anomalies"] == 1
    report = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert report.context["detector"] == ANOMALY_EVAL_GATE


# -------------------------------------------------------------- middleware


class _MemoryCounts:
    def __init__(self) -> None:
        self.count = 0


async def _asgi_app_factory(status: int, crash: bool = False):
    async def app(scope, receive, send) -> None:
        if crash:
            raise RuntimeError("unhandled")
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


async def test_error_counter_counts_only_5xx(monkeypatch) -> None:
    from dosadash_api.sentinel_counters import ServerErrorCounterMiddleware

    counts = _MemoryCounts()

    async def fake_count(self) -> None:
        counts.count += 1

    monkeypatch.setattr(ServerErrorCounterMiddleware, "_count", fake_count)
    scope = {"type": "http", "method": "GET", "path": "/x", "headers": []}

    async def receive():
        return {"type": "http.request"}

    sent: list = []

    async def send(message):
        sent.append(message)

    mw = ServerErrorCounterMiddleware(await _asgi_app_factory(200))
    await mw(scope, receive, send)
    assert counts.count == 0

    mw = ServerErrorCounterMiddleware(await _asgi_app_factory(503))
    await mw(scope, receive, send)
    assert counts.count == 1

    mw = ServerErrorCounterMiddleware(await _asgi_app_factory(200, crash=True))
    with pytest.raises(RuntimeError):
        await mw(scope, receive, send)
    assert counts.count == 2  # an escaped exception IS a 500 the client saw
