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


def test_heartbeat_reports_db_status(monkeypatch):
    async def fake_ping() -> bool:
        return True

    monkeypatch.setattr(tasks, "_ping_db", fake_ping)
    result = tasks.heartbeat.run()
    assert result["db"] is True
    assert "at" in result
