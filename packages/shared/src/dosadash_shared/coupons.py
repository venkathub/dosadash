"""Coupon schemas (Phase 7 coupon engine).

The discount math lives server-side in coupon_service (never trusted from
the client); these are the wire shapes for preview, checkout echo, and the
admin CRUD. AI-suggested coupons (Phase 7 promo agent) arrive as inactive
DRAFTs through the same admin activation flow as manual ones.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dosadash_shared.orders import OrderItemIn
from dosadash_shared.schemas import CouponType

# Server-side guardrails — apply to MANUAL and AI_SUGGESTED coupons alike.
MAX_PCT_VALUE = Decimal("50")  # never more than 50% off
MAX_FLAT_VALUE = Decimal("300")  # ₹ cap for flat coupons


class CouponBase(BaseModel):
    code: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    type: CouponType
    value: Decimal = Field(gt=0)
    description: str | None = Field(default=None, max_length=200)
    min_subtotal: Decimal | None = Field(default=None, ge=0)
    max_discount: Decimal | None = Field(default=None, gt=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)

    @field_validator("code")
    @classmethod
    def _uppercase(cls, v: str) -> str:
        return v.upper()


class CouponCreateIn(CouponBase):
    is_active: bool = False


class CouponUpdateIn(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    value: Decimal | None = Field(default=None, gt=0)
    min_subtotal: Decimal | None = Field(default=None, ge=0)
    max_discount: Decimal | None = Field(default=None, gt=0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class CouponOut(CouponBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    source: str
    times_used: int = 0  # populated from redemption counts


class CouponPreviewIn(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    items: list[OrderItemIn] = Field(min_length=1, max_length=30)


class CouponPreviewOut(BaseModel):
    code: str
    description: str | None
    subtotal: Decimal
    discount: Decimal
    gst: Decimal
    total: Decimal
