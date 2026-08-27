"""Fixer run ingest (Phase 14 slice 3): auth, idempotency, report
resolution, and the FIX_FAILED alarm path."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import config
from dosadash_api.db.models import FeedbackEvent, FeedbackReport, FixerRun

INGEST = "/api/v1/internal/fixer-runs"


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _headers(token: str = "test-internal") -> dict:
    return {"X-Internal-Token": token}


def _payload(**overrides) -> dict:
    base = {
        "workflow": "fix",
        "run_id": 987654,
        "run_attempt": 1,
        "issue_number": 120,
        "conclusion": "success",
        "trigger_label": "ai:auto-fix",
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="FIXING",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="8" * 64,
        github_issue_number=120,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def test_requires_token(client, monkeypatch) -> None:
    resp = await client.post(INGEST, json=_payload(), headers=_headers("wrong"))
    assert resp.status_code == 403
    monkeypatch.delenv("API_INTERNAL_API_TOKEN")
    config.get_settings.cache_clear()
    resp = await client.post(INGEST, json=_payload(), headers=_headers())
    assert resp.status_code == 503


async def test_success_run_resolves_report(client, db_session: AsyncSession) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    resp = await client.post(INGEST, json=_payload(), headers=_headers())
    assert resp.status_code == 201
    body = resp.json()
    assert body["report_id"] == report.id
    assert body["duplicate"] is False
    # success run: no FIX_FAILED event
    events = (await db_session.execute(select(FeedbackEvent))).scalars().all()
    assert events == []


async def test_duplicate_replay_noops(client, db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()
    first = await client.post(INGEST, json=_payload(), headers=_headers())
    assert first.status_code == 201
    second = await client.post(INGEST, json=_payload(), headers=_headers())
    assert second.status_code == 201  # response model default; row not duplicated
    assert second.json()["duplicate"] is True
    runs = (await db_session.execute(select(FixerRun))).scalars().all()
    assert len(runs) == 1
    # a re-run ATTEMPT is a distinct row, not a duplicate
    third = await client.post(INGEST, json=_payload(run_attempt=2), headers=_headers())
    assert third.json()["duplicate"] is False


async def test_failed_fix_run_raises_alarm(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    resp = await client.post(INGEST, json=_payload(conclusion="failure"), headers=_headers())
    assert resp.status_code == 201
    event = (await db_session.execute(select(FeedbackEvent))).scalar_one()
    assert event.stage == "FIX_FAILED"
    assert event.actor == "workflow:fix"
    assert event.payload["conclusion"] == "failure"
    await db_session.refresh(report)
    assert report.status == "FIXING"  # timeline-only: labels stay authoritative


async def test_failed_run_after_merge_stays_quiet(client, db_session: AsyncSession) -> None:
    """A late replay/cancelled attempt must not re-alarm a merged fix."""
    report = _report(status="FIXED")
    db_session.add(report)
    await db_session.commit()
    await client.post(INGEST, json=_payload(conclusion="cancelled"), headers=_headers())
    events = (await db_session.execute(select(FeedbackEvent))).scalars().all()
    assert events == []


async def test_verify_run_without_issue(client, db_session: AsyncSession) -> None:
    resp = await client.post(
        INGEST,
        json=_payload(workflow="verify", issue_number=None, model="claude-haiku-4-5"),
        headers=_headers(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["report_id"] is None
    assert body["workflow"] == "verify"


async def test_unknown_issue_stores_unlinked_row(client, db_session: AsyncSession) -> None:
    resp = await client.post(INGEST, json=_payload(issue_number=555), headers=_headers())
    assert resp.status_code == 201
    assert resp.json()["report_id"] is None


async def test_usage_telemetry_stored_and_optional(client, db_session: AsyncSession) -> None:
    """Phase 15 S7: cache/cost fields persist when reported and stay NULL
    when the execution-file parse degraded to the base payload."""
    with_usage = _payload(
        run_id=900101,
        cost_usd=0.62,
        input_tokens=1200,
        cache_read_tokens=88000,
        cache_creation_tokens=9000,
        output_tokens=4100,
    )
    resp = await client.post(INGEST, json=with_usage, headers=_headers())
    assert resp.status_code == 201
    body = resp.json()
    assert body["cost_usd"] == 0.62
    assert body["cache_read_tokens"] == 88000

    bare = _payload(run_id=900102)  # degrade contract: base payload only
    resp = await client.post(INGEST, json=bare, headers=_headers())
    assert resp.status_code == 201
    assert resp.json()["cost_usd"] is None

    rows = (await db_session.execute(select(FixerRun).order_by(FixerRun.run_id))).scalars().all()
    assert rows[0].cache_creation_tokens == 9000
    assert rows[1].cache_read_tokens is None


async def test_review_workflow_runs_ingest(client, db_session: AsyncSession) -> None:
    """Phase 15 S3: reviewer runs land as workflow='review' — visible in
    metrics/spend, and NEVER eligible for the FIX_FAILED alarm path."""
    db_session.add(_report())
    await db_session.commit()
    payload = _payload(
        workflow="review",
        run_id=910001,
        conclusion="failure",  # a failed review must not alarm as a failed fix
        trigger_label=None,
        cost_usd=0.04,
    )
    resp = await client.post(INGEST, json=payload, headers=_headers())
    assert resp.status_code == 201
    assert resp.json()["workflow"] == "review"
    events = (await db_session.execute(select(FeedbackEvent))).scalars().all()
    assert events == []  # no FIX_FAILED from review conclusions
