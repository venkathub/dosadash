"""Promo suggestion schemas (Phase 7: AI combo/discount drafts → admin
approval).

Same architecture as the inventory agent: deterministic MINING decides the
candidate set, the LLM only names/prices/describes, and a guardrail
re-anchors everything to the mined facts and hard value bands before a
human ever sees it. All drafts land INACTIVE/DRAFT — activation is always
an owner decision (existing combo/coupon approval flows).
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from dosadash_shared.schemas import CouponType

PROMO_PROMPT_VERSION = "promo_agent_v1"

# Guardrail bands (the model is TOLD these; the guardrail ENFORCES them).
COMBO_PRICE_BAND = (Decimal("0.85"), Decimal("0.97"))  # × sum of parts
PCT_BAND = (Decimal("5"), Decimal("30"))  # suggester stays under the admin 50% cap
FLAT_BAND = (Decimal("20"), Decimal("150"))
MAX_COMBO_SUGGESTIONS = 3
MAX_COUPON_SUGGESTIONS = 2


class MinedPair(BaseModel):
    """One co-occurrence candidate, mined deterministically from orders."""

    item_ids: list[int] = Field(min_length=2, max_length=2)  # sorted asc
    names: list[str] = Field(min_length=2, max_length=2)
    parts_total: Decimal
    times_ordered: int


class PromoStats(BaseModel):
    """Business context for coupon drafting (provenance, not authority)."""

    slow_day: str  # weekday name with the lowest 90d revenue
    median_aov: Decimal
    existing_codes: list[str] = Field(default_factory=list)


class PromoComboDraft(BaseModel):
    """LLM output — a name+price for a MINED pair, nothing more."""

    item_ids: list[int] = Field(min_length=2, max_length=2)
    name: str = Field(min_length=3, max_length=120)
    price: Decimal = Field(gt=0)
    rationale: str = Field(max_length=200)


class PromoCouponDraft(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    type: CouponType
    value: Decimal = Field(gt=0)
    max_discount: Decimal | None = Field(default=None, gt=0)
    min_subtotal: Decimal | None = Field(default=None, ge=0)
    description: str = Field(min_length=3, max_length=200)
    rationale: str = Field(max_length=200)


class PromoDraftBatch(BaseModel):
    """The LLM's structured output (Hard Rule 3)."""

    combos: list[PromoComboDraft] = Field(default_factory=list, max_length=6)
    coupons: list[PromoCouponDraft] = Field(default_factory=list, max_length=4)


class PromoComboSuggestion(BaseModel):
    """Post-guardrail: anchored to a mined pair, price clamped to the band."""

    item_ids: list[int]
    names: list[str]
    name: str
    price: Decimal
    parts_total: Decimal
    times_ordered: int
    rationale: str


class PromoCouponSuggestion(BaseModel):
    """Post-guardrail: code normalized+deduped, values clamped."""

    code: str
    type: CouponType
    value: Decimal
    max_discount: Decimal | None
    min_subtotal: Decimal | None
    description: str
    rationale: str


class PromoSuggestResult(BaseModel):
    combos: list[PromoComboSuggestion] = Field(default_factory=list)
    coupons: list[PromoCouponSuggestion] = Field(default_factory=list)
    stats: PromoStats | None = None
    model: str | None = None
    prompt_version: str = PROMO_PROMPT_VERSION
    fallback: bool = False  # deterministic drafts because the LLM chain failed
