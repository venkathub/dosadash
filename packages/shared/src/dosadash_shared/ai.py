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
