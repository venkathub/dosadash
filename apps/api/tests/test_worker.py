"""Celery worker config tests (no broker/DB needed)."""

import pytest

pytest.importorskip("celery")

from dosadash_api.worker import tasks  # noqa: E402
from dosadash_api.worker.celery_app import app  # noqa: E402


def test_celery_app_uses_dedicated_broker_settings():
    # Broker/result come from API_CELERY_* env (dedicated noeviction redis),
    # never the shared cache redis DB 0.
    assert app.conf.broker_url != "redis://redis:6379/0"
    assert app.conf.timezone == "Asia/Kolkata"
    assert app.conf.task_acks_late is True


def test_heartbeat_task_registered_and_scheduled():
    assert "ops.heartbeat" in app.tasks
    assert app.conf.beat_schedule["ops-heartbeat"]["task"] == "ops.heartbeat"


def test_nightly_forecast_scheduled_at_2am_ist():
    assert "forecast.nightly_demand" in app.tasks
    entry = app.conf.beat_schedule["nightly-demand-forecast"]
    assert entry["task"] == "forecast.nightly_demand"
    assert entry["schedule"].hour == {2}
    assert app.conf.timezone == "Asia/Kolkata"


def test_nightly_crm_scheduled_at_3am_ist():
    assert "crm.nightly_segments" in app.tasks
    entry = app.conf.beat_schedule["nightly-crm-segments"]
    assert entry["task"] == "crm.nightly_segments"
    assert entry["schedule"].hour == {3}


def test_nightly_review_scoring_scheduled_after_crm():
    """Phase 8: 03:30 IST — after CRM (03:00), local champion + Batch API."""
    assert "reviews.nightly_scoring" in app.tasks
    entry = app.conf.beat_schedule["nightly-review-scoring"]
    assert entry["task"] == "reviews.nightly_scoring"
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {30}


def test_review_batch_poll_scheduled_hourly():
    assert "reviews.batch_poll" in app.tasks
    entry = app.conf.beat_schedule["review-batch-poll"]
    assert entry["task"] == "reviews.batch_poll"
    assert entry["schedule"].minute == {20}
    assert len(entry["schedule"].hour) == 24  # every hour


def test_heartbeat_reports_db_status(monkeypatch):
    async def fake_ping() -> bool:
        return True

    monkeypatch.setattr(tasks, "_ping_db", fake_ping)
    result = tasks.heartbeat.run()
    assert result["db"] is True
    assert "at" in result
