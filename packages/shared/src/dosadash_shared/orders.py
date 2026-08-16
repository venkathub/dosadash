"""Order API schemas (shared: web, bot rendering, Phase 3 agent's OrderDraft
converts into OrderCreateIn — same DB-validated shapes, Hard Rule 2/3)."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dosadash_shared.schemas import ChannelType, OrderState, PaymentStatus


class OrderItemIn(BaseModel):
    item_id: int
    qty: int = Field(ge=1, le=20)
    customizations: dict[str, Any] | None = None


class OrderCreateIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=30)
    address_id: int | None = None


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    name: str = ""
    qty: int
    unit_price: Decimal
    customizations: dict[str, Any] | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    provider_order_id: str | None
    status: PaymentStatus
    signature_verified: bool


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: OrderState
    channel: ChannelType
    subtotal: Decimal
    gst: Decimal
    total: Decimal
    placed_at: datetime
    items: list[OrderItemOut] = []
    payment: PaymentOut | None = None


class StatusUpdateIn(BaseModel):
    status: OrderState


class PayIn(BaseModel):
    payment_id: str
    signature: str
