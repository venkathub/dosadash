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
