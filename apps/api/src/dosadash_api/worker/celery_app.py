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
    # docs/02 §2.4: 02:00 IST per-dish 14-day demand forecast → `forecasts`.
    "nightly-demand-forecast": {
        "task": "forecast.nightly_demand",
        "schedule": crontab(hour=2, minute=0),
    },
    # docs/02 §2.4: 03:00 IST CRM RFM/churn/LTV → `customer_segments`.
    "nightly-crm-segments": {
        "task": "crm.nightly_segments",
        "schedule": crontab(hour=3, minute=0),
    },
    # Phase 6: 02:30 IST (fresh forecasts land at 02:00) inventory agent →
    # draft POs awaiting owner approval.
    "nightly-inventory-po": {
        "task": "inventory.nightly_po",
        "schedule": crontab(hour=2, minute=30),
    },
    # Phase 8: 03:30 IST (after CRM at 03:00) — INT8 champion scores
    # unscored reviews locally; the unconfident residue goes to the
    # provider Batch API at 50% cost.
    "nightly-review-scoring": {
        "task": "reviews.nightly_scoring",
        "schedule": crontab(hour=3, minute=30),
    },
    # Phase 8: hourly ingest of completed Batch API jobs (provider window
    # is 24h; completion usually lands much sooner).
    "review-batch-poll": {
        "task": "reviews.batch_poll",
        "schedule": crontab(minute=20),
    },
    # Phase 13: triage new feedback reports every 15 min — LLM assessment
    # + deterministic verdict, GitHub labels applied (the fixer workflow
    # triggers on them). Cheap when the queue is empty.
    "feedback-triage": {
        "task": "feedback.triage_pending",
        "schedule": crontab(minute="*/15"),
    },
    # Phase 14: reconcile in-flight reports against GitHub truth (labels,
    # issue state, fixer PRs) — heals missed webhook deliveries. Offset
    # from the triage beat so the two never race on the same rows.
    "feedback-github-sync": {
        "task": "feedback.sync_github",
        "schedule": crontab(minute="5-59/15"),
    },
    # Post-Phase-14 (Actions-outage postmortem): fixer dispatch watchdog —
    # detects dispatches GitHub lost (stuck-queued / startup_failure runs),
    # records FIX_STALLED transparency events, and auto-resumes by
    # re-applying the trigger label once GitHub Actions is healthy again.
    # Cheap: exits on one DB query when nothing is dispatched.
    "feedback-fixer-watchdog": {
        "task": "feedback.fixer_watchdog",
        "schedule": crontab(minute="2-59/5"),
    },
    # Phase 15 (docs/15 §S1): the production sentinel — deterministic
    # anomaly detection (healthz fleet / 5xx burst / eval-gate reds) files
    # SYSTEM feedback reports through the existing self-healing intake.
    # Offset from the watchdog (2-59/5) and the sync beat (5-59/15).
    "sentinel-scan": {
        "task": "sentinel.scan",
        "schedule": crontab(minute="4-59/5"),
    },
}
