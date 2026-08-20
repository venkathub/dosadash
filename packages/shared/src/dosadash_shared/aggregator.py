"""Mock-aggregator channel schemas (Phase 7, docs/04 O12).

Simulates a Zomato/Swiggy-class partner integration without partner access:
a signed webhook injects prepaid orders into the SAME order state machine
(multi-channel routing is the point — the KDS/admin see one queue). The
signature scheme mirrors the Razorpay webhook (HMAC-SHA256 of the raw
body), so swapping in a real aggregator later is a credentials change,
not an architecture change.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

MOCK_AGGREGATORS: tuple[str, ...] = ("mockswiggy", "mockzomato")


class AggregatorCustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^\+91\d{10}$")


class AggregatorItemIn(BaseModel):
    """Aggregators sync menus by name — unknown names are refused loudly
    (menu drift between the aggregator listing and the kitchen is real)."""

    name: str = Field(min_length=1, max_length=120)
    qty: int = Field(ge=1, le=20)


class AggregatorWebhookIn(BaseModel):
    aggregator: str
    external_order_id: str = Field(min_length=4, max_length=80)
    customer: AggregatorCustomerIn
    items: list[AggregatorItemIn] = Field(min_length=1, max_length=25)

    @field_validator("aggregator")
    @classmethod
    def _known_aggregator(cls, v: str) -> str:
        if v not in MOCK_AGGREGATORS:
            raise ValueError(f"unknown aggregator {v!r} (supported: {MOCK_AGGREGATORS})")
        return v


class AggregatorOrderOut(BaseModel):
    order_id: int
    external_order_id: str
    status: str
    total: Decimal
    duplicate: bool = False  # webhook retries are idempotent


class AggregatorStatusOut(BaseModel):
    aggregator: str
    external_order_id: str
    order_id: int
    status: str
    eta_predicted: datetime | None = None


class AggregatorSimulateIn(BaseModel):
    count: int = Field(default=1, ge=1, le=5)
