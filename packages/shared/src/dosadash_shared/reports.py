"""Admin reports + CRM schemas (Phase 5, docs/04 O1/O5/O6).

All money values are floats here — these are analytics rollups for charts
and CSVs, not billing documents (bills stay Decimal in orders/payments).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Granularity = Literal["daily", "weekly", "monthly"]


class SalesBucket(BaseModel):
    period: str  # ISO date of the bucket start (IST)
    orders: int
    revenue: float
    gst: float
    aov: float


class SalesReport(BaseModel):
    granularity: Granularity
    days: int
    buckets: list[SalesBucket]
    total_orders: int
    total_revenue: float
    total_gst: float


class DishPnlRow(BaseModel):
    item_id: int
    name: str
    category: str
    qty: int
    revenue: float
    ingredient_cost: float
    cost_source: Literal["recipe", "estimated"]
    margin: float
    margin_pct: float


class DishPnlReport(BaseModel):
    days: int
    rows: list[DishPnlRow]


class ForecastVsActualPoint(BaseModel):
    date: date
    forecast_qty: float | None = None  # None: no forecast scored for this day
    actual_qty: float | None = None  # None: future day
    anomaly: bool = False


class DishAnomaly(BaseModel):
    item_id: int
    name: str
    date: date
    forecast_qty: float
    actual_qty: float
    deviation_pct: float


class ForecastVsActualReport(BaseModel):
    points: list[ForecastVsActualPoint]
    dish_anomalies: list[DishAnomaly]
    model_version: str | None = None  # None until the nightly job has run


class CrmTierSummary(BaseModel):
    tier: str
    users: int
    avg_churn_risk: float
    total_ltv: float


class CrmUserRow(BaseModel):
    user_id: int
    name: str | None = None
    phone: str
    rfm_tier: str
    churn_risk: float
    ltv: float


class CrmReport(BaseModel):
    computed_at: datetime | None = None  # None until the nightly job has run
    tiers: list[CrmTierSummary]
    at_risk: list[CrmUserRow]  # win-back targets: high LTV × high churn risk
