"""Internal sentinel incident intake (Phase 15 S4): auth + the canary
incident riding the exact sentinel filing spine (SYSTEM tier, fingerprint
dedupe, fence)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import config
from dosadash_api.db.models import FeedbackReport

URL = "/api/v1/internal/sentinel/incident"


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _payload(**overrides) -> dict:
    base = {
        "kind": "deploy_canary_failed",
        "subject": "8330cc7deadbeef0000000000000000000000000",
        "title": "deploy canary breach — auto-rollback engaged",
        "evidence": {"probes": 60, "failures": 12, "rollback": "pr_opened"},
    }
    base.update(overrides)
    return base


async def test_requires_token(client, monkeypatch) -> None:
    resp = await client.post(URL, json=_payload(), headers={"X-Internal-Token": "wrong"})
    assert resp.status_code == 403
    monkeypatch.delenv("API_INTERNAL_API_TOKEN")
    config.get_settings.cache_clear()
    resp = await client.post(URL, json=_payload(), headers={"X-Internal-Token": "test-internal"})
    assert resp.status_code == 503


async def test_unknown_kind_rejected(client) -> None:
    resp = await client.post(
        URL,
        json=_payload(kind="made_up_detector"),
        headers={"X-Internal-Token": "test-internal"},
    )
    assert resp.status_code == 422  # allowlist: callers never invent detectors


async def test_files_system_report_and_dedupes(client, db_session: AsyncSession) -> None:
    headers = {"X-Internal-Token": "test-internal"}
    resp = await client.post(URL, json=_payload(), headers=headers)
    assert resp.status_code == 201
    assert resp.json()["filed"] == 1

    report = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert report.reporter_tier == "SYSTEM"
    assert report.context["detector"] == "deploy_canary_failed"
    assert report.context["fingerprint"].startswith("deploy_canary_failed:")
    assert "pr_opened" in report.description  # evidence made it into the body

    # replay (CI retry / same broken deploy) collapses onto the open report
    resp = await client.post(URL, json=_payload(), headers=headers)
    assert resp.json()["filed"] == 0
    assert resp.json()["skipped_open"] == 1
