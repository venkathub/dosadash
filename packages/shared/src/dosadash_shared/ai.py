"""AI feature schemas (Phase 2+): nutrition enrichment contracts.

These are the structured shapes (Hard Rule 3) shared between the AI service
(which produces estimates via litellm) and the core API (which stores them
and runs the owner-verification flow).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NUTRITION_PROMPT_VERSION = "nutrition_v1"


class RecipeContextLine(BaseModel):
    """One recipe line, as context for the LLM (from the recipe mapping)."""

    name: str
    qty: Decimal
    unit: str


class NutritionEstimateRequest(BaseModel):
    """api → ai request: everything the model needs, nothing it doesn't."""

    item_name: str
    category: str
    description: str | None = None
    is_veg: bool = True
    recipe: list[RecipeContextLine] = Field(min_length=1, max_length=40)


class NutritionEstimate(BaseModel):
    """The LLM's structured output — parsed and validated, never free-text."""

    calories_kcal: float = Field(ge=0, le=3000)
    protein_g: float = Field(ge=0, le=300)
    carbs_g: float = Field(ge=0, le=500)
    fat_g: float = Field(ge=0, le=300)
    fiber_g: float = Field(ge=0, le=100)
    per: Literal["serving"] = "serving"
    confidence: float = Field(ge=0, le=1)
    notes: str | None = Field(default=None, max_length=300)


class NutritionEstimateResponse(BaseModel):
    """ai → api response, with provenance for the audit trail / Langfuse."""

    estimate: NutritionEstimate
    model: str
    prompt_version: str = NUTRITION_PROMPT_VERSION


class NutritionEnrichIn(BaseModel):
    """Admin batch request (bounded — this is a synchronous convenience batch;
    bigger backfills go through repeated calls or a later Celery job)."""

    item_ids: list[int] = Field(min_length=1, max_length=10)


# ------------------------------------------------------------------ ETA (Phase 5)


class EtaRequest(BaseModel):
    """api → ai: order composition + clock for the ETA regressor."""

    max_prep_minutes: int = Field(ge=0, le=240)
    total_qty: int = Field(ge=1, le=200)
    n_lines: int = Field(ge=1, le=50)
    placed_at: datetime | None = None  # UTC; defaults to now


class EtaResponse(BaseModel):
    """ai → api: predicted delivery minutes + model provenance."""

    eta_minutes: int = Field(ge=1, le=240)
    model_version: str


class NutritionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    estimate: NutritionEstimate
    status: str
    model: str
    prompt_version: str
    reviewed_by: int | None = None
    updated_at: datetime | None = None


class NutritionEnrichFailure(BaseModel):
    item_id: int
    error: str


class NutritionEnrichOut(BaseModel):
    enriched: list[NutritionOut] = []
    failed: list[NutritionEnrichFailure] = []


class NutritionStatusIn(BaseModel):
    status: Literal["APPROVED", "REJECTED"]


# ------------------------------------------------- recommender (Phase 7)


class RecsRequest(BaseModel):
    """api → ai: who + current cart context. Everything else (order history,
    orderable menu) is read fresh from the DB inside the ai service."""

    user_id: int | None = None
    cart_item_ids: list[int] = Field(default_factory=list, max_length=20)
    k: int = Field(default=6, ge=1, le=12)


class RecItem(BaseModel):
    """One recommendation — always a real, orderable menu item (validated
    against the DB before it leaves the ai service, mirroring Hard Rule 2)."""

    item_id: int
    name: str
    price: Decimal
    is_veg: bool
    score: float


class RecsResponse(BaseModel):
    """ai → api. `source` records which strategy produced the list:
    als | embedding (cold-start w/ cart) | popular (cold-start w/o cart) |
    unavailable (api-side fallback when the ai service is down)."""

    items: list[RecItem] = Field(default_factory=list)
    source: Literal["als", "embedding", "popular", "unavailable"]
    model_version: str | None = None


class CheckoutSuggestion(BaseModel):
    """One checkout add-on: a real orderable item + why it's suggested.
    kind=combo → completes an APPROVED combo; kind=pairing → fills a missing
    attach category (beverage/sweet/snack), ranked by the recommender."""

    item_id: int
    name: str
    price: Decimal
    is_veg: bool
    kind: Literal["combo", "pairing"]
    reason: str = Field(max_length=120)


class CheckoutSuggestResponse(BaseModel):
    suggestions: list[CheckoutSuggestion] = Field(default_factory=list)
    source: Literal["als", "embedding", "popular", "unavailable"]
    model_version: str | None = None
