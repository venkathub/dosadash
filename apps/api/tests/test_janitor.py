"""Maintenance janitor tests (Phase 15 S5): pure detectors + collectors +
the weekly scan filing through the sentinel spine."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import FeedbackReport, MenuItem, MenuItemTranslation
from dosadash_api.services import janitor
from dosadash_api.services.janitor import (
    JANITOR_FLAKY_KIND,
    JANITOR_STALE_KIND,
    classify_flaky,
    classify_stale_approvals,
    classify_translation_backlog,
)

NOW = datetime(2026, 8, 30, 4, 30)


# ------------------------------------------------------------- classifiers


def _run_report(run_id: int, failed_ids: list[str], all_ids: list[str]) -> dict:
    return {
        "id": run_id,
        "case_reports": [
            {
                "id": case_id,
                "accuracy_problems": ["wrong dish"] if case_id in failed_ids else [],
                "tool_violations": [],
                "bypasses": [],
            }
            for case_id in all_ids
        ],
    }


def test_flaky_is_intermittent_not_broken() -> None:
    """A case failing EVERY run is broken (the gate's job); flaky = failed
    ≥2 runs AND passed at least once. One wobble means nothing."""
    ids = ["ord-001", "ord-002", "ord-003"]
    runs = [
        _run_report(3, ["ord-001", "ord-002"], ids),
        _run_report(2, ["ord-001", "ord-002"], ids),
        _run_report(1, ["ord-002"], ids),  # ord-002 fails ALL 3 → broken, excluded
    ]
    anomaly = classify_flaky(runs)
    assert anomaly is not None
    assert anomaly.kind == JANITOR_FLAKY_KIND
    assert anomaly.evidence["wobbling_cases"] == {"ord-001": 2}

    # single failures never alert (flaky-first policy floor)
    assert classify_flaky([_run_report(1, ["ord-003"], ids)]) is None
    assert classify_flaky([]) is None


def test_translation_backlog_threshold() -> None:
    assert classify_translation_backlog(9) is None
    anomaly = classify_translation_backlog(10)
    assert anomaly is not None
    assert anomaly.evidence["draft_count"] == 10


def test_stale_approvals_age_gate() -> None:
    fresh = {"id": 1, "title": "new", "created_at": NOW - timedelta(days=2)}
    stale = {"id": 2, "title": "old", "created_at": NOW - timedelta(days=8)}
    assert classify_stale_approvals([fresh], now=NOW) is None
    anomaly = classify_stale_approvals([fresh, stale], now=NOW)
    assert anomaly is not None
    assert anomaly.kind == JANITOR_STALE_KIND
    assert anomaly.evidence["stale_count"] == 1
    assert anomaly.evidence["oldest"][0]["report_id"] == 2


# -------------------------------------------------------------- collectors


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="CUSTOMER",
        type="BUG",
        status="NEEDS_APPROVAL",
        title="stuck report title",
        description="ten chars or more",
        dedupe_hash="a" * 64,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def test_pending_approvals_excludes_system(db_session: AsyncSession) -> None:
    """The janitor must never count its own kind — SYSTEM reports (incl.
    last week's janitor filings) are out of the stale tally."""
    db_session.add(_report(dedupe_hash="b" * 64))
    db_session.add(_report(reporter_tier="SYSTEM", dedupe_hash="c" * 64))
    db_session.add(_report(status="APPROVED", dedupe_hash="d" * 64))
    await db_session.commit()
    pending = await janitor.pending_approvals(db_session)
    assert len(pending) == 1
    assert pending[0]["title"] == "stuck report title"


async def test_draft_translation_count(db_session: AsyncSession) -> None:
    item = (await db_session.execute(select(MenuItem).limit(1))).scalar_one()
    db_session.add(
        MenuItemTranslation(
            item_id=item.id,
            lang="ta",
            name="மசாலா தோசை",
            status="DRAFT",
            model="gpt-4o-mini",
            prompt_version="menu_translation_v1",
        )
    )
    await db_session.commit()
    assert await janitor.draft_translation_count(db_session) == 1


# --------------------------------------------------------------------- scan


class FakeGitHub:
    enabled = True

    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> int:
        self.created.append({"title": title, "labels": labels})
        return 800 + len(self.created)


async def test_scan_files_system_reports(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    result = await janitor.scan(
        db_session,
        github,
        settings=get_settings(),
        eval_runs=[
            _run_report(2, ["vce-002"], ["vce-002", "ord-001"]),
            _run_report(1, ["vce-002"], ["vce-002", "ord-001"]),
            _run_report(0, [], ["vce-002", "ord-001"]),
        ],
        draft_count=47,
        pending=[],
    )
    assert result["anomalies"] == 2  # flaky + backlog, no stale
    assert result["filed"] == 2
    reports = (await db_session.execute(select(FeedbackReport))).scalars().all()
    assert all(r.reporter_tier == "SYSTEM" for r in reports)
    detectors = {r.context["detector"] for r in reports}
    assert detectors == {JANITOR_FLAKY_KIND, "janitor_translation_backlog"}
    # sentinel spine: [sentinel] titles + sentinel label
    assert all(i["title"].startswith("[sentinel] ") for i in github.created)


async def test_scan_weekly_rerun_collapses(db_session: AsyncSession) -> None:
    github = FakeGitHub()
    kwargs = dict(
        settings=get_settings(),
        eval_runs=[],
        draft_count=99,
        pending=[],
    )
    first = await janitor.scan(db_session, github, **kwargs)
    assert first["filed"] == 1
    second = await janitor.scan(db_session, github, **kwargs)
    assert second["filed"] == 0
    assert second["skipped_open"] == 1  # open twin — no duplicate issue
