"""Persisting review score results — ONE provenance mapping for the three
writers (admin on-demand scoring, the nightly Celery task, the batch-poll
ingest), so they can never drift (Phase 8).

Provenance contract on `reviews`:
- rating-only        → scored_model = RATING_ONLY_MODEL, prompt version kept
- local INT8 champion → scored_model = "local:<model_version>", NO prompt
- live LLM            → scored_model = model, prompt version kept
- Batch API           → scored_model = "batch:<model>", prompt version kept
"""

from datetime import datetime

from dosadash_api.db.models import Review
from dosadash_shared import (
    BATCH_MODEL_PREFIX,
    RATING_ONLY_MODEL,
    ReviewScoreDraft,
    ReviewScoreResponse,
)


def apply_score_result(
    by_id: dict[int, Review], result: ReviewScoreResponse, *, now: datetime
) -> int:
    """Write a live /internal/reviews/score result. The ai response is
    authoritative for WHAT was scored; reviews the api didn't send are
    ignored (the ai must never add rows)."""
    rating_only = set(result.rating_only_ids)
    local = set(result.local_ids)
    scored = 0
    for score in result.scores:
        review = by_id.get(score.review_id)
        if review is None:
            continue
        review.sentiment = score.sentiment
        review.aspects = [a.model_dump() for a in score.aspects]
        if score.review_id in rating_only:
            review.scored_model = RATING_ONLY_MODEL
            review.scored_prompt_version = result.prompt_version
        elif score.review_id in local:
            # INT8 ONNX champion on-CPU — no LLM, no prompt involved
            review.scored_model = result.local_model
            review.scored_prompt_version = None
        else:
            review.scored_model = result.model
            review.scored_prompt_version = result.prompt_version
        review.scored_at = now
        scored += 1
    return scored


def apply_batch_scores(
    by_id: dict[int, Review],
    scores: list[ReviewScoreDraft],
    *,
    model: str,
    prompt_version: str,
    now: datetime,
) -> int:
    """Write Batch API results. A review scored elsewhere between submit
    and poll (e.g. the admin's on-demand button) is NEVER clobbered — the
    earlier score carried fresher context."""
    scored = 0
    for score in scores:
        review = by_id.get(score.review_id)
        if review is None or review.sentiment is not None:
            continue
        review.sentiment = score.sentiment
        review.aspects = [a.model_dump() for a in score.aspects]
        review.scored_model = f"{BATCH_MODEL_PREFIX}{model}"
        review.scored_prompt_version = prompt_version
        review.scored_at = now
        scored += 1
    return scored
