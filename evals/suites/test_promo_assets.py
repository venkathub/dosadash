"""Key-free CI gates for the promo agent guardrail (Phase 7, Hard Rule 5).

The LLM writes names and copy; these gates pin what it can never do:
invent item pairings the mining didn't produce, price a combo outside the
deal band, exceed the coupon value bands, reuse a live code, or bypass the
no-free-food min_subtotal floor. Every persisted draft must also pass the
api's own admin validation — AI suggestions obey the same physics as
humans typing into the form.
"""

import json
from decimal import Decimal
from pathlib import Path

from dosadash_ai.promo.guardrail import (
    deterministic_coupon,
    sanitize_combos,
    sanitize_coupons,
)
from dosadash_api.routers.admin_coupons import validate_coupon_values
from dosadash_shared import (
    COMBO_PRICE_BAND,
    FLAT_BAND,
    MAX_COMBO_SUGGESTIONS,
    MAX_COUPON_SUGGESTIONS,
    PCT_BAND,
    PROMO_PROMPT_VERSION,
    MinedPair,
    PromoDraftBatch,
    PromoStats,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "promo_guardrail.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "promo_agent_v1.md"

DEFAULT_STATS = PromoStats(slow_day="Tuesday", median_aov=Decimal("310"), existing_codes=[])


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _run(case: dict):
    pairs = [MinedPair.model_validate(p) for p in case["pairs"]]
    batch = PromoDraftBatch.model_validate(case["batch"])
    stats = PromoStats.model_validate(case["stats"]) if "stats" in case else DEFAULT_STATS
    return sanitize_combos(batch, pairs), sanitize_coupons(batch, stats)


def test_guardrail_cases_exact():
    for case in _cases():
        combos, coupons = _run(case)
        expect = case["expect"]
        cid = case["id"]
        if "combo_names" in expect:
            assert [c.name for c in combos] == expect["combo_names"], cid
        if "combo_prices" in expect:
            assert [str(c.price) for c in combos] == expect["combo_prices"], cid
        if "combo_count" in expect:
            assert len(combos) == expect["combo_count"], cid
        if "coupon_codes" in expect:
            assert [c.code for c in coupons] == expect["coupon_codes"], cid
        if "coupon_values" in expect:
            assert [str(c.value) for c in coupons] == expect["coupon_values"], cid
        if "coupon_min_subtotals" in expect:
            assert [str(c.min_subtotal) for c in coupons] == expect["coupon_min_subtotals"], cid
        if "coupon_caps_present" in expect:
            assert all(c.max_discount is not None for c in coupons if c.type == "PCT"), cid
        if "coupon_count" in expect:
            assert len(coupons) == expect["coupon_count"], cid


def test_every_sanitized_output_passes_api_validation():
    """Invariant: whatever the guardrail emits must clear the SAME admin
    validation humans face — zero 422s when the api persists drafts."""
    for case in _cases():
        combos, coupons = _run(case)
        for combo in combos:
            low = combo.parts_total * COMBO_PRICE_BAND[0]
            high = combo.parts_total * COMBO_PRICE_BAND[1]
            assert low <= combo.price <= high, f"{case['id']}: {combo.price} outside band"
        for coupon in coupons:
            validate_coupon_values(  # raises HTTPException on violation
                coupon.type, coupon.value, coupon.min_subtotal, coupon.max_discount
            )


def test_bands_are_sane():
    assert PCT_BAND[1] <= Decimal("50")  # under the admin hard cap
    assert FLAT_BAND[1] <= Decimal("300")
    assert MAX_COMBO_SUGGESTIONS <= 5 and MAX_COUPON_SUGGESTIONS <= 3


def test_deterministic_fallback_coupon_is_valid():
    coupon = deterministic_coupon(DEFAULT_STATS)
    validate_coupon_values(coupon.type, coupon.value, coupon.min_subtotal, coupon.max_discount)
    # and it never collides with existing codes
    stats = PromoStats(slow_day="Tuesday", median_aov=Decimal("310"), existing_codes=[coupon.code])
    assert deterministic_coupon(stats).code != coupon.code


def test_free_food_impossible():
    """Adversarial sweep: no crafted batch may produce a below-band combo or
    a FLAT coupon redeemable on a cart smaller than twice its value."""
    pair = MinedPair(item_ids=[1, 2], names=["A", "B"], parts_total=Decimal("100"), times_ordered=9)
    batch = PromoDraftBatch.model_validate(
        {
            "combos": [{"item_ids": [1, 2], "name": "Free", "price": "0.01", "rationale": "x"}],
            "coupons": [
                {
                    "code": "FREE",
                    "type": "FLAT",
                    "value": "99999",
                    "max_discount": None,
                    "min_subtotal": "0",
                    "description": "free food",
                    "rationale": "x",
                }
            ],
        }
    )
    combos = sanitize_combos(batch, [pair])
    assert combos[0].price >= Decimal("85.00")
    coupons = sanitize_coupons(batch, DEFAULT_STATS)
    assert coupons[0].value == FLAT_BAND[1]
    assert coupons[0].min_subtotal >= coupons[0].value * 2


def test_prompt_contract_coherence():
    prompt = " ".join(PROMPT.read_text().split())
    assert "85%" in prompt and "97%" in prompt  # combo band told to the model
    assert "5–30" in prompt or "5-30" in prompt  # PCT band
    assert "20–150" in prompt or "20-150" in prompt  # FLAT band
    assert "ONLY pairs from `candidate_pairs`" in prompt
    assert PROMPT.stem == PROMO_PROMPT_VERSION


def test_case_hygiene():
    cases = _cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    kinds = {c["kind"] for c in cases}
    assert {
        "clean",
        "hallucination",
        "clamp_low",
        "clamp_high",
        "coupon_clamp",
        "code_normalize",
        "overflow",
    } <= kinds
