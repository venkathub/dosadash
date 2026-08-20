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


# ----------------------------------------------------------------- scoring wire

REVIEW_SENTIMENT_PROMPT_VERSION = "review_sentiment_v1"
REVIEW_REPLY_PROMPT_VERSION = "review_reply_v1"

# rating-only reviews (empty text) never reach an LLM — deterministic score
RATING_ONLY_MODEL = "deterministic:rating"

REVIEW_SCORE_CHUNK_SIZE = 10  # reviews per LLM call (ai side fans out)
MAX_REVIEW_SCORE_ITEMS = 200  # api → ai bound per request

# Deterministic reply guardrail: an AI-drafted owner reply must never promise
# compensation the owner didn't approve. Lowercased substring matches.
REPLY_FORBIDDEN_TERMS: tuple[str, ...] = (
    "refund",
    "free ",
    "for free",
    "discount",
    "coupon",
    "voucher",
    "compensat",
    "on the house",
    "money back",
)
MAX_REPLY_CHARS = 600


def rating_only_sentiment(rating: int) -> str:
    """Shared deterministic rollup for text-less reviews (stars only)."""
    return "POSITIVE" if rating >= 4 else "NEGATIVE" if rating <= 2 else "MIXED"


class ReviewScoreSourceItem(BaseModel):
    """One unscored review, as scoring context. `text` may contain PII —
    the ai side redacts phones before anything reaches an LLM (Rule 8)."""

    review_id: int
    rating: int = Field(ge=1, le=5)
    text: str = Field(max_length=2000)


class ReviewScoreRequest(BaseModel):
    reviews: list[ReviewScoreSourceItem] = Field(min_length=1, max_length=MAX_REVIEW_SCORE_ITEMS)


class ReviewScoreDraft(BaseModel):
    """The LLM's structured output for ONE review (Hard Rule 3). The model
    only OBSERVES aspects; the review-level sentiment is recomputed
    deterministically from them (dish-QC philosophy)."""

    review_id: int
    sentiment: OverallSentiment
    aspects: list[AspectLabel] = Field(default_factory=list, max_length=len(REVIEW_ASPECTS))


class ReviewScoreBatch(BaseModel):
    scores: list[ReviewScoreDraft] = Field(
        default_factory=list, max_length=REVIEW_SCORE_CHUNK_SIZE * 2
    )


class ReviewScoreRejection(BaseModel):
    review_id: int
    reason: str


class ReviewScoreResponse(BaseModel):
    """ai → api: sanitized scores + per-review rejections + provenance.
    `model` is per-score-set; rating-only reviews carry RATING_ONLY_MODEL
    in their own field because no LLM ever saw them."""

    scores: list[ReviewScoreDraft] = Field(default_factory=list)
    rating_only_ids: list[int] = Field(default_factory=list)
    rejected: list[ReviewScoreRejection] = Field(default_factory=list)
    model: str | None = None
    prompt_version: str = REVIEW_SENTIMENT_PROMPT_VERSION


# ------------------------------------------------------------------ reply wire


class ReviewReplyRequest(BaseModel):
    """api → ai: draft ONE owner reply. Aspects give the model something
    concrete to acknowledge; no customer identity is ever sent."""

    review_id: int
    rating: int = Field(ge=1, le=5)
    text: str = Field(max_length=2000)
    sentiment: OverallSentiment | None = None
    aspects: list[AspectLabel] = Field(default_factory=list)
    dishes: list[str] = Field(default_factory=list, max_length=10)


class ReviewReplyDraft(BaseModel):
    """LLM structured output: just the reply text."""

    reply: str = Field(min_length=1, max_length=MAX_REPLY_CHARS * 2)


class ReviewReplyResponse(BaseModel):
    reply: str
    model: str | None = None
    prompt_version: str = REVIEW_REPLY_PROMPT_VERSION
    fallback: bool = False  # deterministic template used (LLM failed/guardrailed)


# ------------------------------------------------------------------ admin wire


class AdminReviewOut(BaseModel):
    """Backoffice view — full scoring + reply provenance."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    rating: int
    text: str
    sentiment: str | None = None
    aspects: list[AspectLabel] | None = None
    scored_model: str | None = None
    scored_prompt_version: str | None = None
    scored_at: datetime | None = None
    reply_draft: str | None = None
    reply_draft_model: str | None = None
    owner_reply: str | None = None
    reply_source: str | None = None
    replied_at: datetime | None = None
    created_at: datetime | None = None
    dishes: list[str] = Field(default_factory=list)  # filled by the api from order rows


class AdminReviewListOut(BaseModel):
    reviews: list[AdminReviewOut] = Field(default_factory=list)
    total: int = 0
    unscored: int = 0


class ReviewScoreRunOut(BaseModel):
    scored: int
    rating_only: int
    failed: int
    model: str | None = None


class ReviewReplyPublishIn(BaseModel):
    reply: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)


class ReviewTrendPoint(BaseModel):
    week_start: str  # ISO date (Monday, IST)
    count: int


class ReviewAspectTrend(BaseModel):
    """Weekly complaint counts for one aspect + the alert flag the inbox
    surfaces ('dosa – too oily ↑')."""

    aspect: str
    points: list[ReviewTrendPoint] = Field(default_factory=list)
    alert: bool = False
    top_dishes: list[str] = Field(default_factory=list)


class ReviewTrendsOut(BaseModel):
    weeks: int
    aspects: list[ReviewAspectTrend] = Field(default_factory=list)
