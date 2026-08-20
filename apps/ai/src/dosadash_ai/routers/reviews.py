"""Internal review endpoints (api → ai) — Phase 8.

POST /internal/reviews/score — batch aspect-sentiment tagging. Local-first
since slice 4: the INT8 ONNX LoRA champion (CPU, ₹0/review) scores text
reviews and only its UNCONFIDENT predictions escalate to the LLM chain —
the tiny model handles the bulk, the LLM handles the doubt. Everything
around the models stays deterministic:

- rating-only reviews (no text) never reach any model — scored from stars
- the local model never sees the network and its inputs never leave the
  process; phone numbers are still redacted BEFORE any LLM call (Rule 8)
- local predictions are only trusted when confident (no probability in the
  ambiguity band, ≥1 label fired, no contradictory both-polarity aspect) —
  anything else gets the LLM's second opinion
- if the artifact is missing/corrupt the local path degrades to the LLM
  chain (postmortem #72 pattern: nice-to-haves degrade, never crash)
- LLM guardrail unchanged: hallucinated review_ids dropped, off-registry
  aspects dropped, duplicates deduped, review-level sentiment RECOMPUTED
  from kept aspects (dish-QC philosophy: models observe, verdicts computed)

POST /internal/reviews/draft-reply — one AI-drafted owner reply. Guardrail:
a draft that promises compensation (refund/discount/free/...) or carries
contact data is discarded and a deterministic template reply ships instead —
the model must never give away food the owner didn't approve. Every draft
lands api-side as a backoffice draft: a human approves before publishing.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.llm import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_ai.redaction import REDACTED, redact_phones
from dosadash_ml.finetune.predict import (
    SentimentChampion,
    load_sentiment_champion,
    predict_sentiment,
)
from dosadash_shared import (
    MAX_REPLY_CHARS,
    REPLY_FORBIDDEN_TERMS,
    REVIEW_ASPECTS,
    REVIEW_REPLY_PROMPT_VERSION,
    REVIEW_SCORE_CHUNK_SIZE,
    REVIEW_SENTIMENT_PROMPT_VERSION,
    AspectLabel,
    ReviewReplyDraft,
    ReviewReplyRequest,
    ReviewReplyResponse,
    ReviewScoreBatch,
    ReviewScoreDraft,
    ReviewScoreRejection,
    ReviewScoreRequest,
    ReviewScoreResponse,
    ReviewScoreSourceItem,
    rating_only_sentiment,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/reviews", tags=["internal:reviews"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


# ------------------------------------------------------- local INT8 champion


@lru_cache
def _cached_champion() -> SentimentChampion:
    return load_sentiment_champion(get_settings().model_dir)


def local_champion() -> SentimentChampion | None:
    """The INT8 ONNX champion, or None when unavailable — the LLM chain
    covers everything then (degrade, never crash)."""
    try:
        return _cached_champion()
    except Exception as exc:  # noqa: BLE001 — missing/corrupt artifact
        logger.warning("reviews: local sentiment champion unavailable: %s", exc)
        return None


def draft_from_local_labels(review_id: int, labels: tuple[str, ...]) -> ReviewScoreDraft | None:
    """Deterministic draft from the local model's label set. Returns None
    when the set contradicts itself (both polarities for one aspect) —
    that review escalates to the LLM instead."""
    aspects: list[AspectLabel] = []
    seen: set[str] = set()
    for label in labels:
        aspect, polarity = label.rsplit(":", 1)
        if aspect in seen:  # taste:POSITIVE + taste:NEGATIVE → not trustworthy
            return None
        if aspect not in REVIEW_ASPECTS:  # label space drifted → never serve it
            return None
        seen.add(aspect)
        aspects.append(AspectLabel(aspect=aspect, sentiment=polarity))
    if not aspects:  # empty set is never confident, but belt-and-braces
        return None
    return ReviewScoreDraft(
        review_id=review_id, sentiment=_rollup(aspects, "MIXED"), aspects=aspects
    )


# ------------------------------------------------------------------- scoring


def build_score_messages(reviews: list[ReviewScoreSourceItem]) -> list[dict[str, str]]:
    """System prompt from the versioned file + compact JSON payload.
    Review text is phone-redacted here — nothing upstream of this function
    may assume it happened elsewhere."""
    payload = {
        "reviews": [
            {"review_id": r.review_id, "rating": r.rating, "text": redact_phones(r.text)}
            for r in reviews
        ]
    }
    return [
        {"role": "system", "content": load_prompt(REVIEW_SENTIMENT_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _rollup(aspects: list[AspectLabel], model_sentiment: str) -> str:
    """Deterministic review-level sentiment: computed from kept aspects when
    any exist; the model's own rollup only survives for aspect-less text."""
    polarities = {a.sentiment for a in aspects}
    if polarities == {"POSITIVE"}:
        return "POSITIVE"
    if polarities == {"NEGATIVE"}:
        return "NEGATIVE"
    if len(polarities) == 2:
        return "MIXED"
    return model_sentiment


def sanitize_scores(
    requested: list[ReviewScoreSourceItem], batch: ReviewScoreBatch
) -> tuple[list[ReviewScoreDraft], list[ReviewScoreRejection]]:
    """Re-anchor the LLM's tags to the requested reviews (the request is
    authoritative, the model only observed)."""
    wanted = {r.review_id for r in requested}
    kept: dict[int, ReviewScoreDraft] = {}
    for draft in batch.scores:
        if draft.review_id not in wanted:  # hallucinated review_id → drop
            continue
        if draft.review_id in kept:  # duplicate → first wins
            continue
        aspects: list[AspectLabel] = []
        seen: set[str] = set()
        for a in draft.aspects:
            if a.aspect not in REVIEW_ASPECTS or a.aspect in seen:  # off-registry → drop
                continue
            seen.add(a.aspect)
            aspects.append(AspectLabel(aspect=a.aspect, sentiment=a.sentiment))
        kept[draft.review_id] = ReviewScoreDraft(
            review_id=draft.review_id,
            sentiment=_rollup(aspects, draft.sentiment),
            aspects=aspects,
        )
    scores = [kept[r.review_id] for r in requested if r.review_id in kept]
    rejected = [
        ReviewScoreRejection(review_id=r.review_id, reason="missing from model output")
        for r in requested
        if r.review_id not in kept
    ]
    return scores, rejected


def _chunks(items: list[ReviewScoreSourceItem], size: int) -> Iterator[list[ReviewScoreSourceItem]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@router.post("/score", response_model=ReviewScoreResponse)
async def score_reviews(
    req: ReviewScoreRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> ReviewScoreResponse:
    _check_internal_token(x_internal_token)

    # rating-only reviews: deterministic, no model of any kind, no cost
    rating_only = [r for r in req.reviews if not r.text.strip()]
    with_text = [r for r in req.reviews if r.text.strip()]
    scores: list[ReviewScoreDraft] = [
        ReviewScoreDraft(
            review_id=r.review_id, sentiment=rating_only_sentiment(r.rating), aspects=[]
        )
        for r in rating_only
    ]
    rejected: list[ReviewScoreRejection] = []
    model_used: str | None = None

    # local INT8 champion first (slice 4): confident predictions are final;
    # unconfident/contradictory ones escalate to the LLM chain below
    local_ids: list[int] = []
    local_model: str | None = None
    llm_queue: list[ReviewScoreSourceItem] = with_text
    champion = None if req.force_llm else local_champion()
    if champion is not None and with_text:
        predictions = await asyncio.to_thread(
            predict_sentiment, champion, [r.text for r in with_text]
        )
        llm_queue = []
        for review, prediction in zip(with_text, predictions, strict=True):
            draft = (
                draft_from_local_labels(review.review_id, prediction.labels)
                if prediction.confident
                else None
            )
            if draft is None:
                llm_queue.append(review)
            else:
                scores.append(draft)
                local_ids.append(review.review_id)
        local_model = f"local:{champion.version}" if local_ids else None

    for chunk in _chunks(llm_queue, REVIEW_SCORE_CHUNK_SIZE):
        try:
            parsed, model = await structured_completion(
                messages=build_score_messages(chunk),
                response_model=ReviewScoreBatch,
                trace_name="reviews.score",
                prompt_version=REVIEW_SENTIMENT_PROMPT_VERSION,
                session_id="reviews:score",
                max_tokens=2000,
            )
        except LLMError as exc:  # one dead chunk doesn't sink the batch
            rejected.extend(
                ReviewScoreRejection(review_id=r.review_id, reason=f"LLM chain failed: {exc}")
                for r in chunk
            )
            continue
        model_used = model
        kept, chunk_rejected = sanitize_scores(chunk, parsed)
        scores.extend(kept)
        rejected.extend(chunk_rejected)

    if llm_queue and model_used is None:  # every LLM chunk failed → loud
        raise HTTPException(status_code=502, detail="LLM chain failed for every batch")
    return ReviewScoreResponse(
        scores=scores,
        rating_only_ids=[r.review_id for r in rating_only],
        local_ids=local_ids,
        rejected=rejected,
        model=model_used,
        local_model=local_model,
    )


# ------------------------------------------------------------------- replies

FALLBACK_REPLIES = {
    "POSITIVE": (
        "Thank you so much for the kind words — it means a lot to our kitchen. "
        "We look forward to serving you again soon! — Team DosaDash"
    ),
    "NEGATIVE": (
        "We're really sorry this order fell short. Your feedback has gone straight "
        "to our kitchen team and we will do better next time. — Team DosaDash"
    ),
    "MIXED": (
        "Thank you for the honest feedback — we're glad about what worked and "
        "we're fixing what didn't. Hope to serve you better next time. — Team DosaDash"
    ),
}


def build_reply_messages(req: ReviewReplyRequest) -> list[dict[str, str]]:
    payload = {
        "rating": req.rating,
        "text": redact_phones(req.text),
        "sentiment": req.sentiment,
        "aspects": [{"aspect": a.aspect, "sentiment": a.sentiment} for a in req.aspects],
        "dishes": req.dishes,
    }
    return [
        {"role": "system", "content": load_prompt(REVIEW_REPLY_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def reply_violation(reply: str) -> str | None:
    """Deterministic reply guardrail — the reasons a draft may never ship."""
    cleaned = reply.strip()
    if not cleaned:
        return "empty reply"
    if len(cleaned) > MAX_REPLY_CHARS:
        return "reply too long"
    lowered = cleaned.lower()
    for term in REPLY_FORBIDDEN_TERMS:
        if term in lowered:
            return f"promises compensation ({term.strip()!r}) — owner decides that, never the model"
    if redact_phones(cleaned) != cleaned or REDACTED in cleaned:
        return "contains contact data"
    if "http://" in lowered or "https://" in lowered or "@" in cleaned:
        return "contains links or contact data"
    return None


def _fallback(req: ReviewReplyRequest) -> str:
    sentiment = req.sentiment or rating_only_sentiment(req.rating)
    return FALLBACK_REPLIES.get(sentiment, FALLBACK_REPLIES["MIXED"])


@router.post("/draft-reply", response_model=ReviewReplyResponse)
async def draft_reply(
    req: ReviewReplyRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> ReviewReplyResponse:
    _check_internal_token(x_internal_token)
    try:
        parsed, model = await structured_completion(
            messages=build_reply_messages(req),
            response_model=ReviewReplyDraft,
            trace_name="reviews.draft_reply",
            prompt_version=REVIEW_REPLY_PROMPT_VERSION,
            session_id="reviews:reply",
            max_tokens=400,
        )
    except LLMError:
        return ReviewReplyResponse(reply=_fallback(req), model=None, fallback=True)
    reply = parsed.reply.strip()
    if reply_violation(reply) is not None:
        return ReviewReplyResponse(reply=_fallback(req), model=model, fallback=True)
    return ReviewReplyResponse(reply=reply, model=model, fallback=False)
