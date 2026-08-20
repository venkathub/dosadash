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


# ------------------------------------------------ dish-photo QC (Phase 7)

DISH_QC_PROMPT_VERSION = "dish_qc_v1"


class DishQCIn(BaseModel):
    """api → ai: one plated-dish/packaging photo + what the order says
    should be in it. Images only (same bounds as invoice OCR)."""

    image_base64: str = Field(min_length=8, max_length=10_000_000)  # ~7 MB image
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    expected_dishes: list[str] = Field(min_length=1, max_length=20)
    session_id: str | None = None


class DishQCExtraction(BaseModel):
    """The VLM's structured OBSERVATIONS — never a verdict. The pass/fail
    decision is computed deterministically from these fields (same earned-
    confidence principle as the invoice arithmetic verifier)."""

    is_food_photo: bool
    dishes_seen: list[str] = Field(default_factory=list, max_length=8)
    presentation_issues: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0, le=1)


DishQCVerdict = Literal["PASS", "MISMATCH", "CHECK", "UNREADABLE"]


class DishQCResult(BaseModel):
    """ai → api: deterministic verdict + the evidence it was computed from."""

    verdict: DishQCVerdict
    matched: list[str] = Field(default_factory=list)  # expected dishes seen
    missing: list[str] = Field(default_factory=list)  # expected but not seen
    unexpected: list[str] = Field(default_factory=list)  # seen but not ordered
    issues: list[str] = Field(default_factory=list)
    extraction: DishQCExtraction | None = None
    model: str | None = None
    prompt_version: str = DISH_QC_PROMPT_VERSION
    error: str | None = None
