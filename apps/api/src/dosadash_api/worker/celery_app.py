"""Celery app factory + beat schedule.

Broker/result live on the dedicated `redis-celery` instance (`noeviction`);
the shared cache Redis (allkeys-lru) must never hold task messages.
"""

from celery import Celery
from celery.schedules import crontab

from dosadash_api.config import get_settings

_settings = get_settings()

app = Celery(
    "dosadash",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["dosadash_api.worker.tasks"],
)

app.conf.update(
    # Restaurant-local time: beat entries below read as kitchen hours.
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_acks_late=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86_400,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=25,  # bound memory drift (4 GB VPS budget)
    broker_connection_retry_on_startup=True,
)

app.conf.beat_schedule = {
    # Proof-of-life: hourly DB ping, visible in `docker compose logs worker`.
    "ops-heartbeat": {
        "task": "ops.heartbeat",
        "schedule": crontab(minute=0),
    },
}
