"""CI-safe eval gates for the nutrition capability (no LLM keys needed).

These guard the eval *assets* — golden dataset integrity, prompt/schema
coherence, and the scoring logic — so a broken prompt or dataset fails the
merge (Hard Rule 5) even before live scoring lands as a gate in Phase 4.
"""

import json
from pathlib import Path

from dosadash_ai.prompts import load_prompt
from dosadash_ai.routers.nutrition import build_messages
from dosadash_shared import (
    NUTRITION_PROMPT_VERSION,
    NutritionEstimate,
    NutritionEstimateRequest,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "nutrition.jsonl"
RANGE_KEYS = ("calories_kcal", "protein_g", "carbs_g", "fat_g")


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def test_golden_dataset_parses_and_is_big_enough():
    cases = _cases()
    assert len(cases) >= 8, "keep at least 8 golden nutrition cases"
    names = [c["item_name"] for c in cases]
    assert len(names) == len(set(names)), "duplicate golden case names"


def test_golden_cases_are_valid_requests_with_sane_ranges():
    for case in _cases():
        # every golden input must be a valid AI-service request
        NutritionEstimateRequest(
            item_name=case["item_name"],
            category=case["category"],
            description=case["description"],
            is_veg=case["is_veg"],
            recipe=case["recipe"],
        )
        for key in RANGE_KEYS:
            lo, hi = case["expect"][key]
            assert 0 <= lo < hi, f"{case['item_name']}: bad range for {key}"
        # expected ranges must fit inside the schema's own bounds
        field = NutritionEstimate.model_fields[key]
        assert case["expect"]["calories_kcal"][1] <= 3000
        assert field is not None


def test_prompt_mentions_every_schema_key():
    prompt = load_prompt(NUTRITION_PROMPT_VERSION)
    for key in NutritionEstimate.model_fields:
        assert f'"{key}"' in prompt, f"prompt {NUTRITION_PROMPT_VERSION} missing key {key}"
    assert "JSON" in prompt


def test_build_messages_round_trips_golden_case():
    case = _cases()[0]
    request = NutritionEstimateRequest(
        item_name=case["item_name"],
        category=case["category"],
        description=case["description"],
        is_veg=case["is_veg"],
        recipe=case["recipe"],
    )
    messages = build_messages(request)
    assert messages[0]["role"] == "system"
    payload = json.loads(messages[1]["content"])
    assert payload["name"] == case["item_name"]
    assert len(payload["recipe"]) == len(case["recipe"])


def test_scoring_logic_detects_out_of_range():
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from nutrition_eval import score_case

    good = NutritionEstimate(
        calories_kcal=200, protein_g=5, carbs_g=35, fat_g=5, fiber_g=2, confidence=0.8
    )
    expect = {
        "calories_kcal": [100, 300],
        "protein_g": [2, 10],
        "carbs_g": [15, 60],
        "fat_g": [1, 12],
    }
    ok, problems = score_case(good, expect)
    assert ok and problems == []

    bad = good.model_copy(update={"calories_kcal": 2500})
    ok, problems = score_case(bad, expect)
    assert not ok and "calories_kcal" in problems[0]
