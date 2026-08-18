"""Schema v2 models (docs/06) — Phase 0/1 scope.

Covers auth/CRM/promos/brand_id plus the core commerce tables Phase 1 needs
(menu, orders, payments). Later phases add their own tables via new Alembic
revisions (forecasts, purchase_orders, chat, rag_chunks, eval_runs, ...).

Design notes:
- Native PG enums are created from the shared StrEnums (single source of truth).
- `orders.channel` is an enum column rather than a `channels` lookup table —
  the docs/06 channel set is closed and code-driven.
- `menu_items.embedding vector(1536)` lives here from day 1 (pgvector), so the
  Phase 3 RAG work needs no schema change for menu semantics.
"""

from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dosadash_api.db.base import Base, TimestampMixin
from dosadash_shared import (
    ChannelType,
    CouponType,
    Diet,
    OrderState,
    OtpChannelType,
    PaymentStatus,
    Role,
)


def pg_enum(enum_cls: type, name: str) -> Enum:
    """Native PG enum storing the StrEnum *values*."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


# --------------------------------------------------------------------------- auth / CRM


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    role: Mapped[Role] = mapped_column(pg_enum(Role, "role"), default=Role.CUSTOMER)
    loyalty_points: Mapped[int] = mapped_column(default=0)

    addresses: Mapped[list["Address"]] = relationship(back_populates="user")
    preferences: Mapped["UserPreference | None"] = relationship(back_populates="user")


class OtpRequest(Base):
    __tablename__ = "otp_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone: Mapped[str] = mapped_column(String(16), index=True)
    otp_hash: Mapped[str] = mapped_column(String(128))
    channel: Mapped[OtpChannelType] = mapped_column(pg_enum(OtpChannelType, "otp_channel"))
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Address(TimestampMixin, Base):
    __tablename__ = "addresses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(40), default="Home")
    line1: Mapped[str] = mapped_column(String(255))
    pincode: Mapped[str] = mapped_column(String(10), index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="addresses")


class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    diet: Mapped[Diet | None] = mapped_column(pg_enum(Diet, "diet"))
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    spice_level: Mapped[int | None] = mapped_column()
    language: Mapped[str] = mapped_column(String(8), default="en")

    user: Mapped[User] = relationship(back_populates="preferences")


# --------------------------------------------------------------------------- menu


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class MenuItem(TimestampMixin, Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    category: Mapped[str] = mapped_column(String(60), index=True)
    is_veg: Mapped[bool] = mapped_column(Boolean, default=True)
    contains_onion_garlic: Mapped[bool] = mapped_column(Boolean, default=True)  # False → Jain-ok
    spice_level: Mapped[int] = mapped_column(default=1)
    prep_minutes: Mapped[int] = mapped_column(default=15)
    meal_periods: Mapped[list[str]] = mapped_column(JSONB, default=list)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("5.00"))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    image_url: Mapped[str | None] = mapped_column(String(500))
    embedding: Mapped[Any | None] = mapped_column(Vector(1536))

    recipe: Mapped[list["RecipeIngredient"]] = relationship()
    customizations: Mapped[list["Customization"]] = relationship()

    __table_args__ = (UniqueConstraint("brand_id", "name"),)


class Customization(Base):
    __tablename__ = "customizations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    price_delta: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))


class Combo(TimestampMixin, Base):
    __tablename__ = "combos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    item_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    source: Mapped[str] = mapped_column(
        Enum("MANUAL", "AI_SUGGESTED", name="combo_source"), default="MANUAL"
    )
    status: Mapped[str] = mapped_column(
        Enum("DRAFT", "APPROVED", "REJECTED", name="combo_status"), default="DRAFT"
    )


class Ingredient(TimestampMixin, Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    unit: Mapped[str] = mapped_column(String(20))
    stock_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    reorder_point: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    supplier: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_allergen: Mapped[bool] = mapped_column(Boolean, default=False)


class RecipeIngredient(Base):
    """Single source of truth: drives inventory depletion AND the RAG allergen KB."""

    __tablename__ = "recipe_ingredients"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))

    ingredient: Mapped[Ingredient] = relationship()


# --------------------------------------------------------------------------- orders


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    channel: Mapped[ChannelType] = mapped_column(pg_enum(ChannelType, "channel"))
    status: Mapped[OrderState] = mapped_column(
        pg_enum(OrderState, "order_state"), default=OrderState.PLACED, index=True
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    gst: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    coupon_id: Mapped[int | None] = mapped_column(ForeignKey("coupons.id"))
    eta_predicted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    address_id: Mapped[int | None] = mapped_column(ForeignKey("addresses.id"))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"))
    qty: Mapped[int] = mapped_column(default=1)
    customizations: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
    item: Mapped[MenuItem] = relationship()


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_order_id: Mapped[str | None] = mapped_column(String(120), index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), index=True)
    refund_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"), default=PaymentStatus.CREATED
    )
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- promos


class Coupon(TimestampMixin, Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    type: Mapped[CouponType] = mapped_column(pg_enum(CouponType, "coupon_type"))
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    segment: Mapped[str | None] = mapped_column(String(60))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_limit: Mapped[int | None] = mapped_column()


class CouponRedemption(Base):
    __tablename__ = "coupon_redemptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    coupon_id: Mapped[int] = mapped_column(ForeignKey("coupons.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    redeemed_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("coupon_id", "order_id"),)


# --------------------------------------------------------------------------- ops


class Settings(TimestampMixin, Base):
    """Single-row business settings (hours, delivery pincodes, kitchen pause)."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    business_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery_pincodes: Mapped[list[str]] = mapped_column(ARRAY(String(10)), default=list)
    kitchen_paused: Mapped[bool] = mapped_column(Boolean, default=False)


class NutritionEstimateRecord(TimestampMixin, Base):
    """LLM-drafted nutrition facts per dish (Phase 2) — owner-verified.

    DRAFT rows are backoffice-only; only APPROVED rows surface on the public
    menu. `model`/`prompt_version` give provenance for the audit trail.
    """

    __tablename__ = "nutrition_estimates"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        Enum("DRAFT", "APPROVED", "REJECTED", name="nutrition_status"), default="DRAFT"
    )
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StaffAction(Base):
    """Audit log for admin/staff mutations."""

    __tablename__ = "staff_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class EvalRun(TimestampMixin, Base):
    """One live eval run over the golden sets (Phase 4 LLMOps scoreboard).

    Rows are ingested from `evals/suites/run_live_evals.py --json` output
    (CI posts after every gate run — including failing ones: regressions
    belong on the scoreboard too). Headline metrics are promoted to real
    columns for trends; per-case drill-down stays in JSONB.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(20), default="ci")  # ci | manual
    cases: Mapped[int]
    order_accuracy: Mapped[float] = mapped_column(Float)
    tool_correctness: Mapped[float] = mapped_column(Float)
    guardrail_bypasses: Mapped[int] = mapped_column(default=0)
    guardrail_cases: Mapped[int] = mapped_column(default=0)
    tone: Mapped[float | None] = mapped_column(Float)
    gates_passed: Mapped[bool] = mapped_column(Boolean, index=True)
    failures: Mapped[list[str]] = mapped_column(JSONB, default=list)
    case_reports: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


# --------------------------------------------------------------------------- ML (Phase 5)


class Forecast(Base):
    """Per-dish daily demand forecast (docs/06) — written by the nightly
    Celery scoring task from the MLflow `champion` model; read by admin
    forecast-vs-actual charts and (Phase 6) the inventory agent.

    Unique on (item_id, date): re-scoring a day overwrites the previous
    prediction and stamps the model_version that produced it.
    """

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date_type] = mapped_column(Date, index=True)
    predicted_qty: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("item_id", "date"),)


class CustomerSegment(Base):
    """Nightly CRM scoring output (docs/06): RFM tier + churn risk + LTV.

    One row per user, fully recomputed by the 03:00 Celery job — `computed_at`
    tells the admin CRM tab how fresh the segmentation is.
    """

    __tablename__ = "customer_segments"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    rfm_tier: Mapped[str] = mapped_column(String(20), index=True)
    churn_risk: Mapped[float] = mapped_column(Float)
    ltv: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
