"""Synthetic customer reviews with PLANTED aspect-sentiment labels (Phase 8).

Every review is assembled from (aspect, polarity) templates, so the ground
truth labels are known by construction — that's what makes the LoRA
fine-tune honestly trainable AND honestly benchmarkable against zero-shot
LLM scoring (accuracy is measured against planted labels, not judge vibes).

Design notes:
- Aspects come from the shared `REVIEW_ASPECTS` registry (single source of
  truth with the LLM guardrail and the fine-tune label space).
- Latin-script languages only (en / hinglish / tanglish — the project's
  eval-language convention): keeps DistilBERT's tokenizer in-distribution.
- Ratings correlate with polarity (a 1-star review is nearly all complaints)
  but the LABEL is the planted aspect set, never the rating.
- Planted trend: in the trailing `TREND_DAYS` of the window, Dosa-category
  orders spike "too oily" freshness complaints — gives the admin trend-alert
  feature something real to find.
- ~1% of reviews embed the reviewer's phone number ("call me back") — planted
  PII so the scoring path's redaction (Hard Rule 8) is exercised end-to-end.
- Deterministic: same (users, orders, seed) → identical output. Labels are
  returned to the caller but NEVER seeded into the DB.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random

from dosadash_ml.datagen.orders import SyntheticOrder
from dosadash_ml.datagen.users import SyntheticUser
from dosadash_shared import REVIEW_ASPECTS

TREND_DAYS = 21  # trailing window with the planted "oily dosa" complaint spike
TREND_ASPECT = "freshness"
TREND_CATEGORY_PREFIX = "Dosa"  # matched against item names via the menu categories
_TREND_PROB = 0.35
_PII_PROB = 0.01
_EMPTY_TEXT_PROB = 0.08

# user language → review writing style (Latin script only, docs eval languages)
_LANG_STYLE = {"en": "en", "hi": "hinglish", "ta": "tanglish"}

# P(a mentioned aspect is a complaint | star rating)
_NEG_PROB_BY_RATING = {1: 0.95, 2: 0.85, 3: 0.55, 4: 0.15, 5: 0.05}
_RATING_WEIGHTS = (8, 10, 14, 30, 38)  # 1..5 stars, skewed positive like real platforms

# Personas complain/praise about what they care about (keys from users.PERSONAS)
PERSONA_ASPECT_BIAS: dict[str, dict[str, float]] = {
    "spice_lover": {"spice": 3.0},
    "filter_coffee_daily": {"temperature": 2.5},
    "sweet_tooth": {"taste": 2.0},
    "late_night_snacker": {"delivery": 2.0},
    "vegan_health": {"freshness": 2.5},
    "peanut_allergy_careful": {"freshness": 1.5},
}

# ------------------------------------------------------------------ templates
# {dish} slots are filled from the order's real items — dish-level trend
# signal for the inbox. Aspects without {dish} read naturally order-level.

TEMPLATES: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("taste", "POSITIVE"): {
        "en": (
            "The {dish} tasted amazing, just like home.",
            "Loved the flavour of the {dish}.",
            "{dish} was absolutely delicious.",
        ),
        "hinglish": (
            "{dish} ka taste ekdum mast tha!",
            "Bahut tasty {dish}, ghar jaisa swad.",
        ),
        "tanglish": (
            "{dish} taste vera level!",
            "Semma taste da, {dish} superb.",
        ),
    },
    ("taste", "NEGATIVE"): {
        "en": (
            "The {dish} was bland and tasteless.",
            "Didn't like the taste of the {dish} at all.",
        ),
        "hinglish": (
            "{dish} ka taste bilkul flat tha, maza nahi aaya.",
            "{dish} mein koi swad hi nahi tha.",
        ),
        "tanglish": (
            "{dish} taste-e illa, waste.",
            "{dish} romba mokka taste.",
        ),
    },
    ("portion", "POSITIVE"): {
        "en": (
            "Generous portion size for the {dish}.",
            "The {dish} portion was more than enough for two.",
        ),
        "hinglish": (
            "{dish} ki quantity kaafi acchi thi.",
            "Portion size ekdum solid, paisa vasool.",
        ),
        "tanglish": (
            "{dish} quantity nalla kuduthanga.",
            "Portion semma periya, full ah irundhuchu.",
        ),
    },
    ("portion", "NEGATIVE"): {
        "en": (
            "The {dish} portion was tiny for the price.",
            "Portion size of the {dish} keeps shrinking.",
        ),
        "hinglish": (
            "{dish} ki quantity bahut kam thi.",
            "Itna chhota portion, {dish} do bite mein khatam.",
        ),
        "tanglish": (
            "{dish} portion romba chinnadhu.",
            "Quantity konjam da, pathala.",
        ),
    },
    ("packaging", "POSITIVE"): {
        "en": (
            "Neat packaging, nothing spilled.",
            "Packaging was sturdy and leak-proof.",
        ),
        "hinglish": (
            "Packing ekdum solid thi, kuch nahi gira.",
            "Bahut acchi packing, sab intact aaya.",
        ),
        "tanglish": (
            "Packing neat ah irundhuchu, spill aagala.",
            "Super packing, chutney ellam safe ah vandhuchu.",
        ),
    },
    ("packaging", "NEGATIVE"): {
        "en": (
            "The sambar leaked all over the bag.",
            "Packaging was flimsy and the box arrived crushed.",
        ),
        "hinglish": (
            "Packing kharab thi, sambar pura leak ho gaya.",
            "Box crush ho ke aaya, packing weak hai.",
        ),
        "tanglish": (
            "Sambar full ah leak aayiduchu, packing mokka.",
            "Box crush aagi vandhuchu, packing weak.",
        ),
    },
    ("delivery", "POSITIVE"): {
        "en": (
            "Delivery was quick, arrived before the ETA.",
            "Rider was on time and polite.",
        ),
        "hinglish": (
            "Delivery time pe ho gayi, bahut fast.",
            "Ekdum jaldi deliver hua, rider bhi polite tha.",
        ),
        "tanglish": (
            "Delivery speed ah vandhuchu, ETA kku munnadi.",
            "Rider on-time ah vandhaar, nalla service.",
        ),
    },
    ("delivery", "NEGATIVE"): {
        "en": (
            "Delivery took forever, almost an hour late.",
            "The order arrived very late and no updates from the rider.",
        ),
        "hinglish": (
            "Delivery bahut late thi, ek ghanta wait kiya.",
            "Order itna late aaya, bhook mar gayi.",
        ),
        "tanglish": (
            "Delivery romba late, oru mani neram wait pannen.",
            "Order vera late ah vandhuchu, patience pochu.",
        ),
    },
    ("price", "POSITIVE"): {
        "en": (
            "Great value for money.",
            "Fair prices for this quality.",
        ),
        "hinglish": (
            "Paisa vasool khana, price bilkul sahi.",
            "Quality ke hisaab se price theek hai.",
        ),
        "tanglish": (
            "Price ku value nalla iruku.",
            "Indha quality ku price correct dhaan.",
        ),
    },
    ("price", "NEGATIVE"): {
        "en": (
            "Too expensive for what you get.",
            "Prices have gone up but quality hasn't.",
        ),
        "hinglish": (
            "Bahut mehenga hai, itne paise ka nahi lagta.",
            "Price zyada hai quality ke comparison mein.",
        ),
        "tanglish": (
            "Price romba jasthi da.",
            "Ivvalavu price ku idhu sari varadhu.",
        ),
    },
    ("freshness", "POSITIVE"): {
        "en": (
            "The {dish} was fresh, straight off the tawa.",
            "Everything tasted fresh and light.",
        ),
        "hinglish": (
            "{dish} ekdum fresh tha, taaza bana hua.",
            "Sab kuch fresh laga, garam garam.",
        ),
        "tanglish": (
            "{dish} fresh ah irundhuchu, appo dhaan senja madhiri.",
            "Ellame fresh, tawa la irundhu direct.",
        ),
    },
    ("freshness", "NEGATIVE"): {
        "en": (
            "The {dish} was way too oily.",
            "{dish} felt stale, like it was made hours ago.",
            "Oil was literally dripping from the {dish}.",
        ),
        "hinglish": (
            "{dish} bahut oily tha, haath chipchipe ho gaye.",
            "{dish} baasi lag raha tha.",
        ),
        "tanglish": (
            "{dish} romba oil ah irundhuchu.",
            "{dish} stale madhiri irundhuchu, fresh illa.",
        ),
    },
    ("spice", "POSITIVE"): {
        "en": (
            "Perfect spice level, exactly how I like it.",
            "The {dish} had a lovely fiery kick.",
        ),
        "hinglish": (
            "Spice level ekdum perfect tha.",
            "{dish} mein mast teekha punch tha.",
        ),
        "tanglish": (
            "Kaaram correct ah irundhuchu, superb.",
            "{dish} la spice level vera level.",
        ),
    },
    ("spice", "NEGATIVE"): {
        "en": (
            "The {dish} was not spicy at all, zero heat.",
            "Way too much chilli in the {dish}, couldn't finish it.",
        ),
        "hinglish": (
            "{dish} mein mirchi bahut zyada thi, khaya nahi gaya.",
            "Bilkul teekha nahi tha, spice level zero.",
        ),
        "tanglish": (
            "{dish} la kaaram adhigam, saapda mudiyala.",
            "Spice-e illa, romba bland.",
        ),
    },
    ("temperature", "POSITIVE"): {
        "en": (
            "Arrived piping hot!",
            "The {dish} was still hot when it reached.",
        ),
        "hinglish": (
            "Khana ekdum garam pahucha.",
            "{dish} bilkul garma garam mila.",
        ),
        "tanglish": (
            "{dish} sooda vandhuchu, super.",
            "Food hot ah dhaan vandhuchu.",
        ),
    },
    ("temperature", "NEGATIVE"): {
        "en": (
            "The {dish} arrived cold.",
            "Food was lukewarm by the time it came.",
        ),
        "hinglish": (
            "{dish} thanda ho ke aaya.",
            "Khana bilkul thanda mil gaya.",
        ),
        "tanglish": (
            "{dish} aariduchu, cold ah vandhuchu.",
            "Food lukewarm ah irundhuchu, sudala.",
        ),
    },
}

_OPENERS = {
    "POSITIVE": {
        "en": ("Great experience overall.", "Will order again!"),
        "hinglish": ("Overall bahut accha experience.", "Phir se order karunga!"),
        "tanglish": ("Overall semma experience.", "Marubadiyum order pannuven!"),
    },
    "NEGATIVE": {
        "en": ("Very disappointed this time.", "Not what I expected."),
        "hinglish": ("Is baar bahut disappoint hua.", "Expectation ke hisaab se bilkul nahi."),
        "tanglish": ("Indha thadava romba disappointment.", "Expect panna madhiri illa."),
    },
}


@dataclass(frozen=True)
class SyntheticAspect:
    """One planted (aspect, polarity) ground-truth tag."""

    aspect: str
    sentiment: str  # POSITIVE | NEGATIVE


@dataclass(frozen=True)
class SyntheticReview:
    order_index: int  # index into the `orders` list passed to generate_reviews
    user_phone: str
    rating: int
    text: str
    language: str  # en | hinglish | tanglish (writing style, Latin script)
    created_at: datetime
    aspects: tuple[SyntheticAspect, ...]  # planted labels — NEVER seeded to DB
    sentiment: str  # POSITIVE | NEGATIVE | MIXED (rollup of planted aspects)


def _overall(aspects: tuple[SyntheticAspect, ...], rating: int) -> str:
    polarities = {a.sentiment for a in aspects}
    if polarities == {"POSITIVE"}:
        return "POSITIVE"
    if polarities == {"NEGATIVE"}:
        return "NEGATIVE"
    if len(polarities) == 2:
        return "MIXED"
    # rating-only review: fall back to the star signal
    return "POSITIVE" if rating >= 4 else "NEGATIVE" if rating <= 2 else "MIXED"


def _aspect_weights(persona_key: str) -> list[float]:
    bias = PERSONA_ASPECT_BIAS.get(persona_key, {})
    return [bias.get(a, 1.0) for a in REVIEW_ASPECTS]


def generate_reviews(
    users: list[SyntheticUser],
    orders: list[SyntheticOrder],
    *,
    seed: int = 42,
    review_rate: float = 0.22,
) -> list[SyntheticReview]:
    """Deterministic reviews for ~review_rate of orders, labels planted."""
    rng = Random(seed)
    by_phone = {u.phone: u for u in users}
    window_end = max((o.placed_at for o in orders), default=datetime.now())
    trend_start = window_end - timedelta(days=TREND_DAYS)

    reviews: list[SyntheticReview] = []
    for idx, order in enumerate(orders):
        if rng.random() >= review_rate:
            continue
        user = by_phone[order.user_phone]
        lang = _LANG_STYLE.get(user.language, "en")
        dishes = [line.item_name for line in order.items]
        created_at = (
            order.placed_at
            + timedelta(minutes=order.delivered_minutes)
            + timedelta(minutes=rng.randrange(60, 48 * 60))
        )

        rating = rng.choices((1, 2, 3, 4, 5), weights=_RATING_WEIGHTS, k=1)[0]

        # rating-only review (no text → nothing for the models to score)
        if rng.random() < _EMPTY_TEXT_PROB:
            reviews.append(
                SyntheticReview(
                    order_index=idx,
                    user_phone=order.user_phone,
                    rating=rating,
                    text="",
                    language=lang,
                    created_at=created_at,
                    aspects=(),
                    sentiment=_overall((), rating),
                )
            )
            continue

        # planted trend: trailing-window "oily dosa" complaint spike
        forced: list[SyntheticAspect] = []
        trend_dish = next((d for d in dishes if TREND_CATEGORY_PREFIX in d), None)
        if trend_dish is not None and order.placed_at >= trend_start and rng.random() < _TREND_PROB:
            forced.append(SyntheticAspect(TREND_ASPECT, "NEGATIVE"))
            rating = min(rating, 3)

        n_aspects = rng.choices((1, 2, 3), weights=(45, 40, 15), k=1)[0]
        weights = _aspect_weights(user.persona.key)
        chosen: dict[str, str] = {a.aspect: a.sentiment for a in forced}
        p_neg = _NEG_PROB_BY_RATING[rating]
        while len(chosen) < n_aspects:
            aspect = rng.choices(REVIEW_ASPECTS, weights=weights, k=1)[0]
            if aspect in chosen:
                continue
            chosen[aspect] = "NEGATIVE" if rng.random() < p_neg else "POSITIVE"

        aspects = tuple(SyntheticAspect(a, s) for a, s in chosen.items())
        sentences: list[str] = []
        overall = _overall(aspects, rating)
        if overall in _OPENERS and rng.random() < 0.5:
            sentences.append(rng.choice(_OPENERS[overall][lang]))
        for a in aspects:
            template = rng.choice(TEMPLATES[(a.aspect, a.sentiment)][lang])
            if a.aspect == TREND_ASPECT and a.sentiment == "NEGATIVE" and trend_dish:
                dish = trend_dish
            else:
                dish = rng.choice(dishes)
            sentences.append(template.format(dish=dish))
        # planted PII: exercises the scoring path's phone redaction (Rule 8)
        if rng.random() < _PII_PROB:
            sentences.append(f"Call me back on {order.user_phone}.")

        reviews.append(
            SyntheticReview(
                order_index=idx,
                user_phone=order.user_phone,
                rating=rating,
                text=" ".join(sentences),
                language=lang,
                created_at=created_at,
                aspects=aspects,
                sentiment=overall,
            )
        )
    return reviews
