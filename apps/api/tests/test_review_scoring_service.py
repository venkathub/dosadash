"""Review score persistence service (Phase 8 slice 5) — pure mapping tests,
no DB: the three writers (admin on-demand, nightly task, batch poller)
share this one provenance implementation."""

from datetime import UTC, datetime

from dosadash_api.db.models import Review
from dosadash_api.services.review_scoring import apply_batch_scores, apply_score_result
from dosadash_shared import (
    RATING_ONLY_MODEL,
    AspectLabel,
    ReviewScoreDraft,
    ReviewScoreResponse,
)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _review(rid: int, rating: int = 2, text: str = "Late.") -> Review:
    return Review(id=rid, order_id=rid, user_id=1, rating=rating, text=text)


def _draft(rid: int, sentiment: str = "NEGATIVE") -> ReviewScoreDraft:
    return ReviewScoreDraft(
        review_id=rid,
        sentiment=sentiment,
        aspects=[AspectLabel(aspect="delivery", sentiment="NEGATIVE")],
    )


def test_apply_score_result_maps_all_three_provenances():
    by_id = {1: _review(1, rating=5, text=""), 2: _review(2), 3: _review(3)}
    result = ReviewScoreResponse(
        scores=[
            ReviewScoreDraft(review_id=1, sentiment="POSITIVE", aspects=[]),
            _draft(2),
            _draft(3),
        ],
        rating_only_ids=[1],
        local_ids=[2],
        model="gpt-4o-mini",
        local_model="local:dosadash-sentiment/v2-int8",
    )
    assert apply_score_result(by_id, result, now=NOW) == 3
    assert by_id[1].scored_model == RATING_ONLY_MODEL
    assert by_id[2].scored_model == "local:dosadash-sentiment/v2-int8"
    assert by_id[2].scored_prompt_version is None
    assert by_id[3].scored_model == "gpt-4o-mini"
    assert by_id[3].scored_prompt_version == "review_sentiment_v1"
    assert all(by_id[i].scored_at == NOW for i in (1, 2, 3))


def test_apply_score_result_ignores_reviews_the_api_never_sent():
    by_id = {1: _review(1)}
    result = ReviewScoreResponse(scores=[_draft(1), _draft(999)], model="gpt-4o-mini")
    assert apply_score_result(by_id, result, now=NOW) == 1


def test_apply_batch_scores_provenance_and_prefix():
    by_id = {7: _review(7)}
    scored = apply_batch_scores(
        by_id, [_draft(7)], model="gpt-4o-mini", prompt_version="review_sentiment_v1", now=NOW
    )
    assert scored == 1
    assert by_id[7].scored_model == "batch:gpt-4o-mini"
    assert by_id[7].scored_prompt_version == "review_sentiment_v1"
    assert by_id[7].sentiment == "NEGATIVE"


def test_apply_batch_scores_never_clobbers_an_existing_score():
    """A review scored on-demand between submit and poll keeps its score —
    the earlier one carried fresher context."""
    review = _review(9)
    review.sentiment = "POSITIVE"
    review.scored_model = "gpt-4o-mini"
    scored = apply_batch_scores(
        {9: review},
        [_draft(9)],
        model="gpt-4o-mini",
        prompt_version="review_sentiment_v1",
        now=NOW,
    )
    assert scored == 0
    assert review.sentiment == "POSITIVE"
    assert review.scored_model == "gpt-4o-mini"
