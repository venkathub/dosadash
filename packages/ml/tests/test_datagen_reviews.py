"""Phase 8 review datagen: determinism, planted labels, trend, PII plant."""

from collections import Counter
from datetime import timedelta

from dosadash_ml.datagen import generate_orders, generate_reviews, generate_users
from dosadash_ml.datagen.reviews import (
    TEMPLATES,
    TREND_ASPECT,
    TREND_DAYS,
    SyntheticReview,
)
from dosadash_shared import REVIEW_ASPECTS, REVIEW_SENTIMENTS

USERS = generate_users(n=120, seed=7)
ORDERS = generate_orders(USERS, days=120, seed=7)
REVIEWS = generate_reviews(USERS, ORDERS, seed=7)


# ------------------------------------------------------------------ basics


def test_deterministic():
    again = generate_reviews(USERS, ORDERS, seed=7)
    assert again == REVIEWS
    assert generate_reviews(USERS, ORDERS, seed=8) != REVIEWS


def test_review_rate_in_expected_band():
    rate = len(REVIEWS) / len(ORDERS)
    assert 0.17 <= rate <= 0.27, rate


def test_reviews_reference_real_orders_and_users():
    phones = {u.phone for u in USERS}
    for r in REVIEWS:
        assert 0 <= r.order_index < len(ORDERS)
        assert r.user_phone == ORDERS[r.order_index].user_phone
        assert r.user_phone in phones


# ------------------------------------------------------------------ labels


def test_planted_aspects_come_from_the_shared_registry():
    for r in REVIEWS:
        for a in r.aspects:
            assert a.aspect in REVIEW_ASPECTS, a
            assert a.sentiment in ("POSITIVE", "NEGATIVE")
        assert r.sentiment in REVIEW_SENTIMENTS


def test_overall_sentiment_rolls_up_from_aspects():
    for r in REVIEWS:
        if not r.aspects:
            continue
        polarities = {a.sentiment for a in r.aspects}
        expected = (
            "POSITIVE"
            if polarities == {"POSITIVE"}
            else "NEGATIVE"
            if polarities == {"NEGATIVE"}
            else "MIXED"
        )
        assert r.sentiment == expected


def test_rating_correlates_with_polarity():
    """1–2 star reviews must be mostly complaints, 5-star mostly praise —
    otherwise the fine-tune learns nothing coherent."""
    neg_low = [a.sentiment for r in REVIEWS if r.rating <= 2 for a in r.aspects]
    pos_high = [a.sentiment for r in REVIEWS if r.rating == 5 for a in r.aspects]
    assert neg_low.count("NEGATIVE") / len(neg_low) > 0.7
    assert pos_high.count("POSITIVE") / len(pos_high) > 0.8


def test_empty_text_reviews_have_no_aspect_labels():
    empty = [r for r in REVIEWS if r.text == ""]
    assert empty, "expected some rating-only reviews"
    assert all(r.aspects == () for r in empty)


# ------------------------------------------------------------------ text


def test_latin_script_only():
    """DistilBERT-friendly by design: en/hinglish/tanglish, no Tamil script."""
    for r in REVIEWS:
        assert r.language in ("en", "hinglish", "tanglish")
        assert all(ord(ch) < 0x0530 for ch in r.text), r.text


def test_templates_cover_every_aspect_polarity_language():
    langs = {"en", "hinglish", "tanglish"}
    for aspect in REVIEW_ASPECTS:
        for polarity in ("POSITIVE", "NEGATIVE"):
            cell = TEMPLATES[(aspect, polarity)]
            assert set(cell) == langs, (aspect, polarity)
            assert all(len(v) >= 2 for v in cell.values()), (aspect, polarity)


# ------------------------------------------------------------------ plants


def test_oily_dosa_trend_planted_in_trailing_window():
    window_end = max(o.placed_at for o in ORDERS)
    trend_start = window_end - timedelta(days=TREND_DAYS)

    def trend_rate(reviews: list[SyntheticReview], in_window: bool) -> float:
        pool = [
            r
            for r in REVIEWS
            if (ORDERS[r.order_index].placed_at >= trend_start) == in_window
            and any("Dosa" in line.item_name for line in ORDERS[r.order_index].items)
            and r.text
        ]
        hits = [
            r
            for r in pool
            if any(a.aspect == TREND_ASPECT and a.sentiment == "NEGATIVE" for a in r.aspects)
        ]
        return len(hits) / max(len(pool), 1)

    assert trend_rate(REVIEWS, in_window=True) > 2 * trend_rate(REVIEWS, in_window=False)


def test_planted_pii_reviews_exist_and_carry_the_reviewer_phone():
    pii = [r for r in REVIEWS if "+91" in r.text]
    assert pii, "expected ~1% planted PII reviews"
    assert all(r.user_phone in r.text for r in pii)
    # rare, or the redaction story would dominate the dataset
    assert len(pii) / len(REVIEWS) < 0.05


def test_language_mix_present():
    counts = Counter(r.language for r in REVIEWS)
    assert set(counts) == {"en", "hinglish", "tanglish"}
