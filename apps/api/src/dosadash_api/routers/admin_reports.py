"""Admin reports (Phase 5, docs/04 O5 + O1): sales, dish P&L, GST CSV,
forecast-vs-actual with anomaly flags.

All rollups are computed in restaurant-local time (Asia/Kolkata) — GST
filing and "how was Saturday?" both mean IST days, not UTC days.
"""

import csv
import io
import re
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import User
from dosadash_api.db.session import get_session
from dosadash_shared import (
    DishAnomaly,
    DishPnlReport,
    DishPnlRow,
    ForecastVsActualPoint,
    ForecastVsActualReport,
    Role,
    SalesBucket,
    SalesReport,
)

router = APIRouter(prefix="/api/v1/admin/reports", tags=["admin:reports"])

AdminUser = require_role(Role.ADMIN, Role.OWNER)

IST_DAY = "(o.placed_at AT TIME ZONE 'Asia/Kolkata')::date"

# Anomaly thresholds (docs/04 O1): relative + absolute floor so quiet days
# don't false-positive on tiny numbers.
ANOMALY_REL = 0.35
ANOMALY_ABS = 10.0
DISH_ANOMALY_REL = 0.5
DISH_ANOMALY_MIN_FORECAST = 3.0

ESTIMATED_FOOD_COST_RATIO = 0.35  # fallback when ingredient costs are unset


@router.get("/sales", response_model=SalesReport)
async def sales_report(
    session: Annotated[AsyncSession, Depends(get_session)],
    granularity: Literal["daily", "weekly", "monthly"] = "daily",
    days: Annotated[int, Query(ge=1, le=730)] = 30,
    admin: User = AdminUser,
) -> SalesReport:
    trunc = {"daily": "day", "weekly": "week", "monthly": "month"}[granularity]
    rows = (
        await session.execute(
            text(
                f"""
                SELECT date_trunc('{trunc}', o.placed_at AT TIME ZONE 'Asia/Kolkata')::date
                           AS period,
                       COUNT(*) AS orders,
                       SUM(o.total) AS revenue,
                       SUM(o.gst) AS gst
                FROM orders o
                WHERE o.status != 'CANCELLED'
                  AND o.placed_at >= now() - make_interval(days => :days)
                GROUP BY period ORDER BY period
                """
            ),
            {"days": days},
        )
    ).fetchall()
    buckets = [
        SalesBucket(
            period=r.period.isoformat(),
            orders=int(r.orders),
            revenue=round(float(r.revenue), 2),
            gst=round(float(r.gst), 2),
            aov=round(float(r.revenue) / int(r.orders), 2),
        )
        for r in rows
    ]
    return SalesReport(
        granularity=granularity,
        days=days,
        buckets=buckets,
        total_orders=sum(b.orders for b in buckets),
        total_revenue=round(sum(b.revenue for b in buckets), 2),
        total_gst=round(sum(b.gst for b in buckets), 2),
    )


@router.get("/dish-pnl", response_model=DishPnlReport)
async def dish_pnl(
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    admin: User = AdminUser,
) -> DishPnlReport:
    """Per-dish P&L. Ingredient cost comes from the recipe mapping when the
    owner has priced ingredients; otherwise a labeled 35% food-cost estimate
    (cost_source tells the UI which is which)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT m.id, m.name, m.category, m.price,
                       SUM(oi.qty) AS qty,
                       SUM(oi.qty * oi.unit_price) AS revenue,
                       (SELECT SUM(ri.qty * ing.cost)
                          FROM recipe_ingredients ri
                          JOIN ingredients ing ON ing.id = ri.ingredient_id
                         WHERE ri.item_id = m.id AND ing.cost IS NOT NULL) AS recipe_cost
                FROM order_items oi
                JOIN orders o ON o.id = oi.order_id
                JOIN menu_items m ON m.id = oi.item_id
                WHERE o.status != 'CANCELLED'
                  AND o.placed_at >= now() - make_interval(days => :days)
                GROUP BY m.id ORDER BY revenue DESC
                """
            ),
            {"days": days},
        )
    ).fetchall()
    out: list[DishPnlRow] = []
    for r in rows:
        qty, revenue = int(r.qty), float(r.revenue)
        if r.recipe_cost is not None:
            unit_cost, source = float(r.recipe_cost), "recipe"
        else:
            unit_cost, source = float(r.price) * ESTIMATED_FOOD_COST_RATIO, "estimated"
        cost = round(unit_cost * qty, 2)
        margin = round(revenue - cost, 2)
        out.append(
            DishPnlRow(
                item_id=r.id,
                name=r.name,
                category=r.category,
                qty=qty,
                revenue=round(revenue, 2),
                ingredient_cost=cost,
                cost_source=source,
                margin=margin,
                margin_pct=round(margin / revenue * 100, 1) if revenue else 0.0,
            )
        )
    return DishPnlReport(days=days, rows=out)


@router.get("/gst.csv")
async def gst_csv(
    session: Annotated[AsyncSession, Depends(get_session)],
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    admin: User = AdminUser,
) -> Response:
    """GST filing export: one row per non-cancelled order in the IST month."""
    month = month or date.today().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    rows = (
        await session.execute(
            text(
                f"""
                SELECT o.id, {IST_DAY} AS day, o.channel, o.subtotal, o.gst, o.total
                FROM orders o
                WHERE o.status != 'CANCELLED'
                  AND to_char(o.placed_at AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM') = :month
                ORDER BY o.placed_at
                """
            ),
            {"month": month},
        )
    ).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["order_id", "date", "channel", "taxable_value_inr", "gst_inr", "total_inr"])
    for r in rows:
        writer.writerow([r.id, r.day.isoformat(), r.channel, r.subtotal, r.gst, r.total])
    writer.writerow([])
    writer.writerow(
        [
            "TOTALS",
            month,
            f"{len(rows)} orders",
            sum(r.subtotal for r in rows),
            sum(r.gst for r in rows),
            sum(r.total for r in rows),
        ]
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="gst-{month}.csv"'},
    )


@router.get("/forecast-vs-actual", response_model=ForecastVsActualReport)
async def forecast_vs_actual(
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=60)] = 14,
    admin: User = AdminUser,
) -> ForecastVsActualReport:
    """Aggregate daily forecast vs actual (past `days` + any scored future),
    with day-level and dish-level anomaly flags (docs/04 O1)."""
    today = date.today()
    start = today - timedelta(days=days)

    actual_rows = (
        await session.execute(
            text(
                f"""
                SELECT {IST_DAY} AS day, SUM(oi.qty) AS qty
                FROM order_items oi JOIN orders o ON o.id = oi.order_id
                WHERE o.status != 'CANCELLED' AND o.placed_at >= :start
                GROUP BY day
                """
            ),
            {"start": start},
        )
    ).fetchall()
    forecast_rows = (
        await session.execute(
            text(
                "SELECT date, SUM(predicted_qty) AS qty FROM forecasts"
                " WHERE date >= :start GROUP BY date"
            ),
            {"start": start},
        )
    ).fetchall()
    version = await session.scalar(
        text("SELECT model_version FROM forecasts ORDER BY created_at DESC LIMIT 1")
    )

    actual = {r.day: float(r.qty) for r in actual_rows}
    forecast = {r.date: float(r.qty) for r in forecast_rows}
    points: list[ForecastVsActualPoint] = []
    for day in sorted(set(actual) | set(forecast)):
        a, f = actual.get(day), forecast.get(day)
        anomaly = (
            day < today
            and a is not None
            and f is not None
            and abs(a - f) / max(f, 1.0) > ANOMALY_REL
            and abs(a - f) >= ANOMALY_ABS
        )
        points.append(
            ForecastVsActualPoint(date=day, forecast_qty=f, actual_qty=a, anomaly=anomaly)
        )

    dish_rows = (
        await session.execute(
            text(
                f"""
                WITH actual AS (
                    SELECT oi.item_id, {IST_DAY} AS day, SUM(oi.qty) AS qty
                    FROM order_items oi JOIN orders o ON o.id = oi.order_id
                    WHERE o.status != 'CANCELLED' AND o.placed_at >= :start
                    GROUP BY oi.item_id, day
                )
                SELECT f.item_id, m.name, f.date, f.predicted_qty,
                       COALESCE(a.qty, 0) AS actual_qty
                FROM forecasts f
                JOIN menu_items m ON m.id = f.item_id
                LEFT JOIN actual a ON a.item_id = f.item_id AND a.day = f.date
                WHERE f.date >= :start AND f.date < :today
                  AND f.predicted_qty >= :min_forecast
                """
            ),
            {"start": start, "today": today, "min_forecast": DISH_ANOMALY_MIN_FORECAST},
        )
    ).fetchall()
    anomalies = []
    for r in dish_rows:
        f, a = float(r.predicted_qty), float(r.actual_qty)
        deviation = abs(a - f) / max(f, 1.0)
        if deviation > DISH_ANOMALY_REL:
            anomalies.append(
                DishAnomaly(
                    item_id=r.item_id,
                    name=r.name,
                    date=r.date,
                    forecast_qty=round(f, 1),
                    actual_qty=a,
                    deviation_pct=round(deviation * 100, 1),
                )
            )
    anomalies.sort(key=lambda d: d.deviation_pct, reverse=True)
    return ForecastVsActualReport(
        points=points, dish_anomalies=anomalies[:8], model_version=version
    )
