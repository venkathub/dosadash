"""Live nutrition eval — scores the nutrition prompt against golden ranges.

Requires real LLM keys (OPENAI_API_KEY etc.); NOT run in CI (CI runs the
key-free asset gates in test_nutrition_assets.py — live scoring becomes a
merge gate in Phase 4 alongside order_accuracy).

Usage:
    uv run python evals/suites/nutrition_eval.py            # full chain
    PASS_THRESHOLD=0.8 uv run python evals/suites/nutrition_eval.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dosadash_ai.llm import structured_completion
from dosadash_ai.routers.nutrition import build_messages
from dosadash_shared import (
    NUTRITION_PROMPT_VERSION,
    NutritionEstimate,
    NutritionEstimateRequest,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "nutrition.jsonl"
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))
RANGE_KEYS = ("calories_kcal", "protein_g", "carbs_g", "fat_g")


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def score_case(estimate: NutritionEstimate, expect: dict) -> tuple[bool, list[str]]:
    problems = []
    for key in RANGE_KEYS:
        lo, hi = expect[key]
        value = getattr(estimate, key)
        if not lo <= value <= hi:
            problems.append(f"{key}={value} outside [{lo}, {hi}]")
    if estimate.confidence < 0.2:
        problems.append(f"confidence suspiciously low: {estimate.confidence}")
    return (not problems, problems)


async def run() -> int:
    cases = load_golden()
    passed = 0
    for case in cases:
        request = NutritionEstimateRequest(
            item_name=case["item_name"],
            category=case["category"],
            description=case["description"],
            is_veg=case["is_veg"],
            recipe=case["recipe"],
        )
        estimate, model = await structured_completion(
            messages=build_messages(request),
            response_model=NutritionEstimate,
            trace_name="eval.nutrition",
            prompt_version=NUTRITION_PROMPT_VERSION,
            session_id="eval:nutrition",
        )
        ok, problems = score_case(estimate, case["expect"])
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case['item_name']} ({model}): {problems or 'in range'}")
        passed += ok

    rate = passed / len(cases)
    print(f"\nnutrition eval: {passed}/{len(cases)} passed ({rate:.0%})")
    print(f"threshold: {PASS_THRESHOLD:.0%}")
    return 0 if rate >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
