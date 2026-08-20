"""Review schemas (Phase 8: fine-tune + reviews inbox).

Trust model: customers write free-text reviews; an aspect-sentiment model
(zero-shot LLM first, LoRA fine-tune later — the benchmark IS the point)
auto-tags them for the owner's inbox. The ASPECT REGISTRY below is the single
source of truth shared by the synthetic datagen (planted training labels),
the LLM guardrail (hallucinated aspects are dropped, never served), and the
fine-tune label space — a key-free eval gate enforces this coherence.

Planted ground-truth labels from datagen NEVER land in the DB: the reviews
table only stores what a real system would have (rating + text), so scoring
models are evaluated against held-out datagen labels, not leaked ones.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------- registry

# Aspect vocabulary — adding an aspect means retraining the fine-tune and
# updating the prompt; keep it small and learnable (eval-gate coherence).
REVIEW_ASPECTS: tuple[str, ...] = (
    "taste",
    "portion",
    "packaging",
    "delivery",
    "price",
    "freshness",
    "spice",
    "temperature",
)

# Per-aspect polarity is binary (a mention is praise or a complaint);
# the review-level rollup adds MIXED when both polarities appear.
AspectPolarity = Literal["POSITIVE", "NEGATIVE"]
OverallSentiment = Literal["POSITIVE", "NEGATIVE", "MIXED"]

REVIEW_SENTIMENTS: tuple[str, ...] = ("POSITIVE", "NEGATIVE", "MIXED")


class AspectLabel(BaseModel):
    """One (aspect, polarity) tag — the atomic unit of the whole Phase 8
    story: planted by datagen, predicted by models, shown in the inbox."""

    aspect: str = Field(min_length=1, max_length=20)
    sentiment: AspectPolarity


# ---------------------------------------------------------------- customer wire


class ReviewCreateIn(BaseModel):
    """Customer review for one DELIVERED order (one per order)."""

    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=2000)


class ReviewOut(BaseModel):
    """Customer-facing view — scoring internals (sentiment/aspects/drafts)
    are backoffice-only and deliberately absent here."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    rating: int
    text: str
    owner_reply: str | None = None
    replied_at: datetime | None = None
    created_at: datetime | None = None
