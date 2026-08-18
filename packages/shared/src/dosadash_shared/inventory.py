"""Inventory schemas (Phase 6): suppliers, purchase orders, wastage log.

Structured inputs/outputs everywhere (Hard Rule 3): the admin web UI, the
inventory agent (`PODraft` is its structured output — never free text), the
Telegram approval flow, and tests all share these shapes.

PO lifecycle (transitions only via the api purchase-order service):

    DRAFT → PENDING_APPROVAL → APPROVED → RECEIVED
                     ↓             ↓
                 REJECTED      CANCELLED
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class POState(StrEnum):
    """Purchase-order lifecycle (docs/06 Phase 6)."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class POSource(StrEnum):
    """Who drafted the PO: the nightly inventory agent or a human."""

    AGENT = "AGENT"
    MANUAL = "MANUAL"


WastageReason = Literal["SPOILAGE", "PREP_LOSS", "SPILLAGE", "EXPIRED", "OTHER"]


# ------------------------------------------------------------------ suppliers


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=16)
    email: str | None = Field(default=None, max_length=120)
    lead_time_days: int = Field(default=1, ge=0, le=30)
    is_active: bool = True


class SupplierUpdateIn(BaseModel):
    """Partial update — only fields explicitly set are applied."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=16)
    email: str | None = Field(default=None, max_length=120)
    lead_time_days: int | None = Field(default=None, ge=0, le=30)
    is_active: bool | None = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None = None
    email: str | None = None
    lead_time_days: int
    is_active: bool


# ---------------------------------------------------------------- wastage log


class WastageIn(BaseModel):
    ingredient_id: int
    qty: Decimal = Field(gt=0, le=Decimal("10000"))
    reason: WastageReason
    note: str | None = Field(default=None, max_length=300)


class WastageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ingredient_id: int
    ingredient_name: str
    unit: str
    qty: Decimal
    reason: str
    note: str | None = None
    recorded_by: int
    stock_after: Decimal
    at: datetime


# ------------------------------------------------------------ purchase orders


class PODraftLine(BaseModel):
    """One agent-proposed order line. `ingredient_id` MUST exist in the DB —
    the guardrail rejects drafts containing unknown ingredients (Hard Rule 2
    analog: no hallucinated ingredients, ever)."""

    ingredient_id: int
    qty: Decimal = Field(gt=0, le=Decimal("100000"))
    reason: str = Field(min_length=3, max_length=200)


class PODraft(BaseModel):
    """Structured output of the inventory agent LLM pass (Hard Rule 3)."""

    supplier_id: int | None = None
    lines: list[PODraftLine] = Field(min_length=1, max_length=60)
    rationale: str = Field(min_length=3, max_length=1000)

    @field_validator("lines")
    @classmethod
    def _unique_ingredients(cls, v: list[PODraftLine]) -> list[PODraftLine]:
        ids = [line.ingredient_id for line in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate ingredient_id in PO lines")
        return v


class POItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ingredient_id: int
    ingredient_name: str
    unit: str
    qty: Decimal
    unit_cost: Decimal | None = None
    reason: str | None = None


class PurchaseOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int | None = None
    supplier_name: str | None = None
    status: POState
    source: POSource
    rationale: str | None = None
    coverage_days: int
    expected_cost: Decimal | None = None
    model: str | None = None
    prompt_version: str | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None
    received_at: datetime | None = None
    created_at: datetime


class PurchaseOrderDetailOut(PurchaseOrderOut):
    items: list[POItemOut] = []


class POItemPatchIn(BaseModel):
    """Owner line-item edit before approval."""

    qty: Decimal = Field(gt=0, le=Decimal("100000"))
