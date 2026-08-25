"""Celery tasks. Sync task bodies wrap async DB work via asyncio.run —
each task creates and disposes its own engine (prefork workers must not
share asyncpg connections across event loops).
"""

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dosadash_api.config import get_settings
from dosadash_api.db.models import Forecast
from dosadash_api.worker.celery_app import app
from dosadash_ml.forecasting.features import ItemMeta
from dosadash_ml.forecasting.predict import ForecastRow, forecast_next_days, load_champion

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
HISTORY_DAYS = 60  # covers lag_14 + ma_7 warm-up with slack


async def _ping_db() -> bool:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    finally:
        await engine.dispose()


@app.task(name="ops.heartbeat")
def heartbeat() -> dict[str, Any]:
    """Hourly proof-of-life: worker up + DB reachable."""
    db_ok = asyncio.run(_ping_db())
    result = {"at": datetime.now(UTC).isoformat(), "db": db_ok}
    logger.info("heartbeat %s", result)
    return result


# ------------------------------------------------------------------ forecasts


async def _load_history(
    engine: AsyncEngine, *, start: date, end: date
) -> tuple[dict[int, list[float]], dict[int, ItemMeta]]:
    """Dense per-item daily qty series over [start, end] + item metas.

    Demand = everything the kitchen was asked to cook: all orders except
    CANCELLED (REFUNDED orders were still cooked).
    """
    async with engine.connect() as conn:
        items = (await conn.execute(text("SELECT id, category, is_veg FROM menu_items"))).fetchall()
        sales = (
            await conn.execute(
                text(
                    """
                    SELECT oi.item_id, (o.placed_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
                           SUM(oi.qty) AS qty
                    FROM order_items oi
                    JOIN orders o ON o.id = oi.order_id
                    WHERE o.status != 'CANCELLED'
                      AND o.placed_at >= :start AND o.placed_at < :cutoff
                    GROUP BY oi.item_id, day
                    """
                ),
                {"start": start, "cutoff": end + timedelta(days=1)},
            )
        ).fetchall()

    metas = {r.id: ItemMeta(item_id=r.id, category=r.category, is_veg=r.is_veg) for r in items}
    by_key = {(r.item_id, r.day): float(r.qty) for r in sales}
    n_days = (end - start).days + 1
    history = {
        item_id: [by_key.get((item_id, start + timedelta(days=i)), 0.0) for i in range(n_days)]
        for item_id in metas
    }
    return history, metas


async def _upsert_forecasts(engine: AsyncEngine, rows: list[ForecastRow], version: str) -> None:
    async with engine.begin() as conn:
        stmt = pg_insert(Forecast).values(
            [
                {
                    "item_id": r.item_id,
                    "date": r.date,
                    "predicted_qty": r.predicted_qty,
                    "model_version": version,
                }
                for r in rows
            ]
        )
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["item_id", "date"],
                set_={
                    "predicted_qty": stmt.excluded.predicted_qty,
                    "model_version": stmt.excluded.model_version,
                    "created_at": text("now()"),
                },
            )
        )


async def _score_demand(horizon: int) -> dict[str, Any]:
    settings = get_settings()
    model = load_champion(settings.model_dir)
    today = datetime.now(IST).date()
    engine = create_async_engine(settings.database_url)
    try:
        history, metas = await _load_history(
            engine, start=today - timedelta(days=HISTORY_DAYS), end=today - timedelta(days=1)
        )
        rows = forecast_next_days(model, history, metas, start=today, horizon=horizon)
        await _upsert_forecasts(engine, rows, model.version)
    finally:
        await engine.dispose()
    return {"model_version": model.version, "items": len(metas), "rows": len(rows)}


@app.task(name="forecast.nightly_demand", bind=True, max_retries=2, default_retry_delay=300)
def nightly_demand(self: Any, horizon: int | None = None) -> dict[str, Any]:
    """02:00 IST: score the champion model → upsert `forecasts` (idempotent)."""
    try:
        result = asyncio.run(_score_demand(horizon or get_settings().forecast_horizon_days))
    except Exception as exc:  # transient DB/broker hiccups → bounded retry
        logger.exception("nightly_demand failed")
        raise self.retry(exc=exc) from exc
    logger.info("nightly_demand %s", result)
    return result


# ------------------------------------------------------------------ CRM segments


async def _score_segments() -> dict[str, Any]:
    """Aggregate 365d of orders per user → RFM/churn/LTV → customer_segments."""
    from dosadash_api.db.models import CustomerSegment
    from dosadash_api.worker.crm import UserAggregate, score_segments

    now = datetime.now(UTC)
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT o.user_id,
                               COUNT(*) AS n_orders,
                               SUM(o.total) AS total_spend,
                               MIN(o.placed_at) AS first_order_at,
                               MAX(o.placed_at) AS last_order_at
                        FROM orders o
                        WHERE o.status != 'CANCELLED'
                          AND o.placed_at >= :since
                        GROUP BY o.user_id
                        """
                    ),
                    {"since": now - timedelta(days=365)},
                )
            ).fetchall()

        aggregates = [
            UserAggregate(
                user_id=r.user_id,
                n_orders=int(r.n_orders),
                total_spend=float(r.total_spend),
                first_order_at=r.first_order_at,
                last_order_at=r.last_order_at,
            )
            for r in rows
        ]
        scores = score_segments(aggregates, now=now)
        if scores:
            async with engine.begin() as conn:
                stmt = pg_insert(CustomerSegment).values(
                    [
                        {
                            "user_id": s.user_id,
                            "rfm_tier": s.rfm_tier,
                            "churn_risk": s.churn_risk,
                            "ltv": s.ltv,
                            "computed_at": now,
                        }
                        for s in scores
                    ]
                )
                await conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["user_id"],
                        set_={
                            "rfm_tier": stmt.excluded.rfm_tier,
                            "churn_risk": stmt.excluded.churn_risk,
                            "ltv": stmt.excluded.ltv,
                            "computed_at": stmt.excluded.computed_at,
                        },
                    )
                )
    finally:
        await engine.dispose()
    tiers: dict[str, int] = {}
    for s in scores:
        tiers[s.rfm_tier] = tiers.get(s.rfm_tier, 0) + 1
    return {"users": len(scores), "tiers": tiers}


@app.task(name="crm.nightly_segments", bind=True, max_retries=2, default_retry_delay=300)
def nightly_segments(self: Any) -> dict[str, Any]:
    """03:00 IST: recompute customer_segments (idempotent full refresh)."""
    try:
        result = asyncio.run(_score_segments())
    except Exception as exc:
        logger.exception("nightly_segments failed")
        raise self.retry(exc=exc) from exc
    logger.info("nightly_segments %s", result)
    return result


# ------------------------------------------------------------------ inventory (Phase 6)


async def _publish_inventory(event_type: str, detail: dict[str, Any]) -> None:
    """Fresh Redis client per call: the worker runs each task in its own
    event loop, so the api module's lru-cached client must not be reused."""
    import json

    from redis.asyncio import Redis

    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        from dosadash_api.events import INVENTORY_CHANNEL

        await redis.publish(INVENTORY_CHANNEL, json.dumps({"type": event_type, "detail": detail}))
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning("inventory event publish failed (%s)", event_type, exc_info=True)
    finally:
        await redis.aclose()


async def _draft_inventory_pos(coverage_days: int) -> dict[str, Any]:
    """Call the ai inventory agent, persist drafts as PENDING_APPROVAL POs."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from dosadash_api.services import po_service
    from dosadash_api.services.ai_client import get_ai_client
    from dosadash_shared import InventoryDraftRequest

    result = await get_ai_client().draft_inventory_pos(
        InventoryDraftRequest(coverage_days=coverage_days, session_id="inventory-nightly")
    )
    if not result.drafts:
        return {"needs": len(result.needs), "created": 0, "skipped": 0, "fallback": result.fallback}

    from dosadash_api.services.po_notify import notify_owners_po_drafted

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            created, skipped = await po_service.persist_agent_drafts(session, result)
            await session.commit()
            created_ids = [po.id for po in created]
            notified = await notify_owners_po_drafted(session, created_ids)
    finally:
        await engine.dispose()

    for po_id in created_ids:
        await _publish_inventory("inventory.po_drafted", {"po_id": po_id})
    return {
        "needs": len(result.needs),
        "created": len(created_ids),
        "po_ids": created_ids,
        "skipped": len(skipped),
        "fallback": result.fallback,
        "violations": len(result.violations),
        "notified": notified,
    }


@app.task(name="inventory.nightly_po", bind=True, max_retries=2, default_retry_delay=300)
def nightly_po(self: Any, coverage_days: int = 7) -> dict[str, Any]:
    """02:30 IST (after the 02:00 forecast refresh): stock vs forecast →
    agent draft POs → PENDING_APPROVAL, awaiting the owner. Idempotent:
    suppliers with an open agent PO are skipped."""
    try:
        result = asyncio.run(_draft_inventory_pos(coverage_days))
    except Exception as exc:
        logger.exception("nightly_po failed")
        raise self.retry(exc=exc) from exc
    logger.info("nightly_po %s", result)
    return result


# ------------------------------------------------------------------ reviews (Phase 8)


async def _nightly_review_scoring(limit: int) -> dict[str, Any]:
    """Local INT8 champion scores what it is confident about at ₹0; the
    residue goes to the provider Batch API at 50% of live pricing. The
    live LLM is never called from this task (defer_llm)."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from dosadash_api.db.models import Review, ReviewBatchJob
    from dosadash_api.services.ai_client import AIServiceError, get_ai_client
    from dosadash_api.services.review_scoring import apply_score_result
    from dosadash_shared import (
        ReviewBatchSubmitRequest,
        ReviewScoreRequest,
        ReviewScoreSourceItem,
    )

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            # reviews already in a SUBMITTED batch are in flight — never
            # double-submit (idempotence across nightly runs)
            in_flight: set[int] = set()
            for chunks in (
                await session.execute(
                    select(ReviewBatchJob.chunks).where(ReviewBatchJob.status == "SUBMITTED")
                )
            ).scalars():
                in_flight.update(rid for chunk in chunks for rid in chunk)

            rows = (
                (
                    await session.execute(
                        select(Review)
                        .where(Review.sentiment.is_(None))
                        .order_by(Review.created_at.asc())
                        .limit(limit + len(in_flight))
                    )
                )
                .scalars()
                .all()
            )
            rows = [r for r in rows if r.id not in in_flight][:limit]
            if not rows:
                return {"scored": 0, "deferred": 0, "batch": None}

            ai = get_ai_client()
            result = await ai.score_reviews(
                ReviewScoreRequest(
                    reviews=[
                        ReviewScoreSourceItem(review_id=r.id, rating=r.rating, text=r.text)
                        for r in rows
                    ],
                    defer_llm=True,
                )
            )
            by_id = {r.id: r for r in rows}
            scored = apply_score_result(by_id, result, now=datetime.now(UTC))
            # commit local/rating-only scores BEFORE the batch submit: a
            # provider failure must never roll back work already done
            await session.commit()

            deferred_ids = set(result.deferred_ids)
            deferred = [r for r in rows if r.id in deferred_ids]
            batch_id: str | None = None
            if deferred:
                try:
                    submitted = await ai.batch_submit_reviews(
                        ReviewBatchSubmitRequest(
                            reviews=[
                                ReviewScoreSourceItem(review_id=r.id, rating=r.rating, text=r.text)
                                for r in deferred
                            ]
                        )
                    )
                except AIServiceError:
                    # unscored reviews simply wait for the next nightly run
                    logger.warning("review batch submit failed", exc_info=True)
                else:
                    session.add(
                        ReviewBatchJob(
                            provider="openai",
                            provider_batch_id=submitted.provider_batch_id,
                            model=submitted.model,
                            prompt_version=submitted.prompt_version,
                            chunks=submitted.chunks,
                            n_reviews=submitted.n_reviews,
                            status="SUBMITTED",
                        )
                    )
                    await session.commit()
                    batch_id = submitted.provider_batch_id
            return {
                "scored": scored,
                "local": len(result.local_ids),
                "rating_only": len(result.rating_only_ids),
                "deferred": len(deferred),
                "batch": batch_id,
            }
    finally:
        await engine.dispose()


@app.task(name="reviews.nightly_scoring", bind=True, max_retries=2, default_retry_delay=300)
def nightly_review_scoring(self: Any, limit: int = 200) -> dict[str, Any]:
    """03:30 IST: score unscored reviews — INT8 champion locally, residue
    to the Batch API. Idempotent: in-flight reviews are skipped."""
    try:
        result = asyncio.run(_nightly_review_scoring(limit))
    except Exception as exc:
        logger.exception("nightly_review_scoring failed")
        raise self.retry(exc=exc) from exc
    logger.info("nightly_review_scoring %s", result)
    return result


async def _poll_review_batches() -> dict[str, Any]:
    """Walk SUBMITTED batch jobs; ingest completed output through the same
    guardrail as live scoring (the ai side sanitizes)."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from dosadash_api.db.models import Review, ReviewBatchJob
    from dosadash_api.services.ai_client import AIServiceError, get_ai_client
    from dosadash_api.services.review_scoring import apply_batch_scores
    from dosadash_shared import ReviewBatchPollRequest

    completed = failed = in_progress = 0
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            jobs = (
                (
                    await session.execute(
                        select(ReviewBatchJob).where(ReviewBatchJob.status == "SUBMITTED")
                    )
                )
                .scalars()
                .all()
            )
            ai = get_ai_client()
            now = datetime.now(UTC)
            for job in jobs:
                try:
                    poll = await ai.batch_poll_reviews(
                        ReviewBatchPollRequest(
                            provider_batch_id=job.provider_batch_id, chunks=job.chunks
                        )
                    )
                except AIServiceError:
                    logger.warning(
                        "review batch poll failed (%s)", job.provider_batch_id, exc_info=True
                    )
                    continue
                if poll.status == "in_progress":
                    in_progress += 1
                    continue
                if poll.status == "failed":
                    job.status = "FAILED"
                    job.error = poll.error
                    job.completed_at = now
                    failed += 1
                    continue
                ids = [rid for chunk in job.chunks for rid in chunk]
                rows = (
                    (await session.execute(select(Review).where(Review.id.in_(ids))))
                    .scalars()
                    .all()
                )
                job.scored = apply_batch_scores(
                    {r.id: r for r in rows},
                    poll.scores,
                    model=poll.model or job.model,
                    prompt_version=job.prompt_version,
                    now=now,
                )
                job.failed = len(poll.rejected)
                job.status = "COMPLETED"
                job.completed_at = now
                completed += 1
            await session.commit()
    finally:
        await engine.dispose()
    return {"completed": completed, "failed": failed, "in_progress": in_progress}


@app.task(name="reviews.batch_poll", bind=True, max_retries=1, default_retry_delay=300)
def review_batch_poll(self: Any) -> dict[str, Any]:
    """Hourly: ingest completed Batch API jobs (idempotent — a review
    scored elsewhere in the meantime is never clobbered)."""
    try:
        result = asyncio.run(_poll_review_batches())
    except Exception as exc:
        logger.exception("review_batch_poll failed")
        raise self.retry(exc=exc) from exc
    logger.info("review_batch_poll %s", result)
    return result


# ------------------------------------------------------------- feedback (Phase 13)


async def _triage_pending_feedback(limit: int) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from dosadash_api.services import feedback_triage_runner
    from dosadash_api.services.ai_client import get_ai_client
    from dosadash_api.services.github_client import get_github_client

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await feedback_triage_runner.triage_pending(
                session, get_ai_client(), get_github_client(), limit=limit
            )
    finally:
        await engine.dispose()


@app.task(name="feedback.triage_pending", bind=True, max_retries=1, default_retry_delay=300)
def feedback_triage_pending(self: Any, limit: int = 20) -> dict[str, Any]:
    """Every 15 min: triage untriaged feedback reports (LLM assessment →
    deterministic verdict → GitHub labels). Idempotent: only rows with
    triage IS NULL are examined; ai-unreachable rows are skipped and
    retried next run — a report is never lost."""
    try:
        result = asyncio.run(_triage_pending_feedback(limit))
    except Exception as exc:
        logger.exception("feedback_triage_pending failed")
        raise self.retry(exc=exc) from exc
    logger.info("feedback_triage_pending %s", result)
    return result


# ------------------------------------------------------------- feedback sync (Phase 14)


async def _sync_feedback_github(limit: int) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from dosadash_api.services import feedback_sync
    from dosadash_api.services.github_client import get_github_client

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await feedback_sync.sync_github(session, get_github_client(), limit=limit)
    finally:
        await engine.dispose()


@app.task(name="feedback.sync_github", bind=True, max_retries=1, default_retry_delay=300)
def feedback_sync_github(self: Any, limit: int = 30) -> dict[str, Any]:
    """Every 15 min (offset from triage): reconcile in-flight reports
    against GitHub labels/state/PRs — the webhook's safety net. Idempotent:
    a report already matching GitHub truth is untouched; GitHub-unreachable
    rows are skipped and retried next run."""
    try:
        result = asyncio.run(_sync_feedback_github(limit))
    except Exception as exc:
        logger.exception("feedback_sync_github failed")
        raise self.retry(exc=exc) from exc
    logger.info("feedback_sync_github %s", result)
    return result
