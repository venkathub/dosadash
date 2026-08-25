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
    CheckConstraint,
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
    EscalationStatus,
    InvoiceStatus,
    OrderState,
    OtpChannelType,
    PaymentStatus,
    POSource,
    POState,
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
    image_ai: Mapped[bool] = mapped_column(Boolean, default=False)  # AI-labeled (Phase 7)
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
    supplier: Mapped[str | None] = mapped_column(String(120))  # legacy free-text (display only)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), index=True
    )  # Phase 6: canonical supplier link (backfilled from the free-text column)
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
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
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
    code: Mapped[str] = mapped_column(String(40), unique=True)  # stored UPPERCASE
    type: Mapped[CouponType] = mapped_column(pg_enum(CouponType, "coupon_type"))
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    description: Mapped[str | None] = mapped_column(String(200))
    segment: Mapped[str | None] = mapped_column(String(60))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_limit: Mapped[int | None] = mapped_column()
    # Phase 7 engine fields: guardrails + activation + provenance.
    min_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_discount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # PCT cap
    per_user_limit: Mapped[int | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(
        Enum("MANUAL", "AI_SUGGESTED", name="coupon_source"), default="MANUAL"
    )


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


class MenuItemTranslation(TimestampMixin, Base):
    """LLM-drafted menu localization per (dish, language) — owner-verified
    (Phase 7, Tamil-first).

    Same trust model as nutrition_estimates: drafts are backoffice-only and
    only APPROVED rows will ever be served to customers. Prices/allergens/
    flags are NOT stored here — they always come from the canonical row.
    """

    __tablename__ = "menu_item_translations"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    lang: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    category_label: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(
        Enum("DRAFT", "APPROVED", "REJECTED", name="translation_status"), default="DRAFT"
    )
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class MenuImageDraft(TimestampMixin, Base):
    """AI-generated dish photo awaiting owner review (Phase 7) — AI-labeled.

    The file lives under the api media dir; only an explicit approval copies
    its URL onto menu_items.image_url (with image_ai = true so the customer
    UI always shows the AI badge). Rejection deletes the file.
    """

    __tablename__ = "menu_image_drafts"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True
    )
    filename: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        Enum("DRAFT", "APPROVED", "REJECTED", name="image_draft_status"), default="DRAFT"
    )
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    prompt: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AggregatorOrder(Base):
    """(aggregator, external_order_id) → our order (Phase 7 mock channel).

    Makes webhook delivery idempotent and status polling possible; the
    order itself lives in `orders` with channel = MOCK_AGGREGATOR.
    """

    __tablename__ = "aggregator_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    aggregator: Mapped[str] = mapped_column(String(40))
    external_order_id: Mapped[str] = mapped_column(String(80))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("aggregator", "external_order_id"),)


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


class UserMemory(Base):
    """Long-term agent memory (Phase 6): episodic store beyond session
    checkpoints. `EPISODE` rows are order summaries written by order_service
    on every placed order; the ai context loader reads the latest few (and
    derives "my usual" from order history) for logged-in customers."""

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=False)
    kind: Mapped[str] = mapped_column(String(20), default="EPISODE")
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- ML (Phase 5)


class Escalation(TimestampMixin, Base):
    """Support-agent inbox (Phase 6): refund requests and anything the agent
    must not resolve itself. A human closes it; resolution may run the real
    provider refund (order_service.refund, admin/owner only)."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=False
    )
    kind: Mapped[str] = mapped_column(String(20))  # refund | support
    status: Mapped[EscalationStatus] = mapped_column(
        pg_enum(EscalationStatus, "escalation_status"), default=EscalationStatus.OPEN, index=True
    )
    customer_message: Mapped[str] = mapped_column(Text)
    agent_summary: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolution_note: Mapped[str | None] = mapped_column(String(300))


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


# --------------------------------------------------------------------------- inventory (Phase 6)


class Supplier(TimestampMixin, Base):
    """Supplier master (Phase 6): promoted from the free-text
    `ingredients.supplier` column (backfilled by migration c9d4e82f7a13).
    Purchase orders reference this table; the legacy text column remains
    for display back-compat."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    phone: Mapped[str | None] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(120))
    lead_time_days: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PurchaseOrder(TimestampMixin, Base):
    """Inventory-agent draft PO (or manual PO) — owner-approved before
    execution. State transitions only via `po_service` (mirrors the order
    state machine convention):

        DRAFT → PENDING_APPROVAL → APPROVED → RECEIVED
                        ↓              ↓
                    REJECTED       CANCELLED

    `model`/`prompt_version`/`rationale` give agent provenance for the audit
    trail (same pattern as nutrition_estimates). RECEIVED increments
    `ingredients.stock_qty`.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[POState] = mapped_column(
        pg_enum(POState, "po_state"), default=POState.DRAFT, index=True
    )
    source: Mapped[POSource] = mapped_column(pg_enum(POSource, "po_source"), default=POSource.AGENT)
    rationale: Mapped[str | None] = mapped_column(Text)
    coverage_days: Mapped[int] = mapped_column(default=7)
    expected_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier: Mapped[Supplier | None] = relationship()
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="po", cascade="all, delete-orphan"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    po_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(20))  # snapshot at draft time
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    reason: Mapped[str | None] = mapped_column(String(200))  # agent's per-line why

    po: Mapped[PurchaseOrder] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (UniqueConstraint("po_id", "ingredient_id"),)


class Invoice(TimestampMixin, Base):
    """Supplier invoice (Phase 6): VLM extraction + PO match, held in a
    confidence-gated review queue. APPROVED → the linked PO is RECEIVED and
    stock moves; the extraction/match JSONB keeps full provenance."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[InvoiceStatus] = mapped_column(
        pg_enum(InvoiceStatus, "invoice_status"), default=InvoiceStatus.PENDING_REVIEW, index=True
    )
    po_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="SET NULL"), index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    match: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(String(300))


class WastageEntry(Base):
    """Wastage log (Phase 6): each entry decrements `ingredients.stock_qty`
    atomically (clamped at 0 — kitchens discover wastage they never counted
    in). `stock_after` snapshots the resulting level for the admin trail."""

    __tablename__ = "wastage_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[str] = mapped_column(
        Enum("SPOILAGE", "PREP_LOSS", "SPILLAGE", "EXPIRED", "OTHER", name="wastage_reason")
    )
    note: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    stock_after: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    ingredient: Mapped[Ingredient] = relationship()


# --------------------------------------------------------------------------- reviews (Phase 8)


class Review(TimestampMixin, Base):
    """Customer review for one DELIVERED order (docs/06) — one per order.

    `sentiment`/`aspects` are written by the scoring path (zero-shot LLM now,
    quantized LoRA later), never by the customer: NULL sentiment = unscored,
    and `scored_model`/`scored_prompt_version` keep provenance so the
    LoRA-vs-API benchmark can be read straight off the table.

    Reply flow mirrors the owner-approval pattern: `reply_draft` is the
    AI-drafted reply (backoffice-only); only an explicit publish copies text
    into `owner_reply` (source AI_DRAFT if the draft shipped, MANUAL if the
    owner wrote their own).
    """

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text, default="")
    # scoring output + provenance (NULL sentiment = not scored yet)
    sentiment: Mapped[str | None] = mapped_column(
        Enum("POSITIVE", "NEGATIVE", "MIXED", name="review_sentiment")
    )
    aspects: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    scored_model: Mapped[str | None] = mapped_column(String(80))
    scored_prompt_version: Mapped[str | None] = mapped_column(String(40))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # owner reply flow
    reply_draft: Mapped[str | None] = mapped_column(Text)
    reply_draft_model: Mapped[str | None] = mapped_column(String(80))
    owner_reply: Mapped[str | None] = mapped_column(Text)
    reply_source: Mapped[str | None] = mapped_column(
        Enum("AI_DRAFT", "MANUAL", name="reply_source")
    )
    replied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)


class ReviewBatchJob(TimestampMixin, Base):
    """One submitted provider Batch API job for review scoring (Phase 8
    slice 5). `chunks` is the authoritative custom_id → review_ids mapping
    recorded at submit time and handed back to the ai service at poll time
    (the ai side stays stateless). SUBMITTED jobs also act as a dedup set:
    the nightly task never re-submits a review that is already in flight.

    Batch provenance lives here rather than in Langfuse — litellm's
    callback does not fire for the files/batches endpoints."""

    __tablename__ = "review_batch_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), default="openai")
    provider_batch_id: Mapped[str] = mapped_column(String(80), unique=True)
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    chunks: Mapped[list[list[int]]] = mapped_column(JSONB)
    n_reviews: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(
        Enum("SUBMITTED", "COMPLETED", "FAILED", name="review_batch_status"),
        default="SUBMITTED",
        index=True,
    )
    scored: Mapped[int | None] = mapped_column()
    failed: Mapped[int | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- feedback (Phase 13)


class FeedbackReport(TimestampMixin, Base):
    """User-raised bug/feature report (Phase 13 self-healing loop, docs/14).

    The api stores the row FIRST (phone-redacted — Hard Rule 8: issue bodies
    leave our infrastructure), then mirrors it to a GitHub issue best-effort
    (hotfix-#72 pattern: a GitHub outage never 5xxes the reporter;
    `github_error` records why the mirror failed so the admin tab can retry).

    `dedupe_hash` collapses repeat reports onto the open original. Triage
    provenance (Slice 3: verdict, model, prompt_version, at) lands in the
    `triage` JSONB; GitHub labels are the authoritative automation signal and
    `status` is the local projection the admin tab reads without a GitHub
    round-trip."""

    __tablename__ = "feedback_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reporter_tier: Mapped[str] = mapped_column(
        Enum("ANON", "CUSTOMER", "STAFF", name="feedback_reporter_tier")
    )
    type: Mapped[str] = mapped_column(Enum("BUG", "FEATURE", name="feedback_type"))
    status: Mapped[str] = mapped_column(
        Enum(
            "RECEIVED",
            "TRACKED",
            "AUTO_FIX",
            "NEEDS_APPROVAL",
            "APPROVED",
            "REJECTED",
            "FIXING",
            "PR_OPEN",
            "FIXED",
            "VERIFIED",
            "REOPENED",
            "DISMISSED",
            name="feedback_status",
        ),
        default="RECEIVED",
        index=True,
    )
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)
    github_issue_number: Mapped[int | None] = mapped_column(index=True)
    github_error: Mapped[str | None] = mapped_column(String(300))
    triage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Phase 14 lifecycle sync: the fixer's PR (once opened) + verifier sign-off.
    fix_pr_number: Mapped[int | None] = mapped_column()
    verified_at: Mapped[datetime | None] = mapped_column()


class FeedbackEvent(Base):
    """Append-only lifecycle timeline for one feedback report (Phase 14).

    Every stage of the self-healing loop — intake, triage, human decision,
    fixer run, PR, merge, verification, reopen — lands here exactly once,
    written by the local pipeline, the GitHub webhook, or the reconciler.
    This table is the single source for the /fixer portal timeline, the
    Telegram lifecycle feed (Slice 2), and all funnel/MTTR metrics
    (Slice 3). `stage` is a String (not a PG enum) on purpose: the stage
    vocabulary will grow with the loop and must never need a migration.

    `delivery_id` carries GitHub's X-GitHub-Delivery GUID so webhook
    redeliveries are idempotent (checked in code, not a DB constraint —
    locally-written events have none)."""

    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_reports.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    delivery_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class FeedbackNotification(TimestampMixin, Base):
    """Telegram lifecycle anchor per (report, admin) — Phase 14 slice 2.

    The bot keeps ONE status card per report per linked admin, edited in
    place on every lifecycle stage (Telegram edits are silent — the full
    timeline stays visible without notification spam); separate ping
    replies fire only for actionable/terminal stages. This row remembers
    the anchor's message_id so the api can ask the bot to edit rather than
    resend. Row missing → the bot sends a fresh card and we store it."""

    __tablename__ = "feedback_notifications"
    __table_args__ = (UniqueConstraint("report_id", "tg_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_reports.id", ondelete="CASCADE"), index=True
    )
    tg_user_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)


class FixerRun(Base):
    """One fixer/verifier workflow run, self-reported by the workflow's
    final ingest step (Phase 14 slice 3 — the eval_runs CI-ingest pattern).

    Run-level truth the GitHub webhooks cannot carry: a run that died
    without opening a PR is invisible to label/PR events — here it lands
    as conclusion='failure' and (for fix runs) raises a FIX_FAILED
    timeline event + Telegram ping. (workflow, run_id, run_attempt) is
    unique so re-run attempts are distinct rows and step retries no-op."""

    __tablename__ = "fixer_runs"
    __table_args__ = (UniqueConstraint("workflow", "run_id", "run_attempt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("feedback_reports.id", ondelete="SET NULL"), index=True
    )
    workflow: Mapped[str] = mapped_column(String(10))  # fix | verify
    run_id: Mapped[int] = mapped_column(BigInteger, index=True)
    run_attempt: Mapped[int] = mapped_column(default=1)
    issue_number: Mapped[int | None] = mapped_column(index=True)
    conclusion: Mapped[str] = mapped_column(String(30))
    trigger_label: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
