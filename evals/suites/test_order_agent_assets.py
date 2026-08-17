"""Key-free asset gates for the order-agent golden conversations (CI).

Live order_accuracy scoring lives in order_agent_eval.py; it becomes the
Phase 4 merge gate (fail if < 0.95, Hard Rule 5). These gates keep the
golden set itself coherent with the seed menu and the prompt contract.
"""

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "order_conversations.jsonl"

REQUIRED_FIELDS = {"id", "language", "kitchen", "history", "draft", "message", "expect", "tags"}
LANGUAGES = {"en", "hinglish", "tanglish"}
TAG_VOCABULARY = {
    "basic",
    "multi_item",
    "edit_qty",
    "remove_item",
    "replace_item",
    "clear_cart",
    "confirm",
    "typo",
    "adversarial",
    "pii",
    "sold_out",
    "kitchen_paused",
    "allergen",
    "hallucination",
    "preference",
    "meal_period",
    "factual",
    "budget",
    "edge",
}
# Phase 4 golden-set coverage floors (docs/05 week 7 deliverable).
MIN_CASES = 80
MIN_PER_TAG = {
    "typo": 6,
    "adversarial": 6,
    "sold_out": 4,
    "kitchen_paused": 3,
    "allergen": 5,
    "hallucination": 3,
    "confirm": 4,
    "meal_period": 4,
}
MIN_PER_LANGUAGE = {"en": 30, "hinglish": 10, "tanglish": 10}


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _menu_names() -> set[str]:
    from dosadash_ml.datagen import MENU_ITEMS

    return {m.name for m in MENU_ITEMS}


def test_cases_have_required_fields():
    for case in load_cases():
        missing = REQUIRED_FIELDS - set(case)
        assert not missing, f"{case.get('id')}: missing {missing}"
        assert case["language"] in LANGUAGES, case["id"]
        assert case["kitchen"] in {"open", "paused"}, case["id"]


def test_ids_unique_and_substantial():
    ids = [c["id"] for c in load_cases()]
    assert len(ids) >= MIN_CASES, f"golden set has {len(ids)} cases, need >= {MIN_CASES}"
    assert len(ids) == len(set(ids))


def test_tags_are_valid():
    for case in load_cases():
        tags = case["tags"]
        assert tags, f"{case['id']}: tags must be non-empty"
        unknown = set(tags) - TAG_VOCABULARY
        assert not unknown, f"{case['id']}: unknown tags {unknown}"


def test_tag_coverage_floors():
    """Phase 4 deliverable: adversarial, typo, sold-out, paused, allergen etc.
    coverage may only grow — floors, not exact counts."""
    cases = load_cases()
    for tag, minimum in MIN_PER_TAG.items():
        count = sum(tag in c["tags"] for c in cases)
        assert count >= minimum, f"tag {tag!r}: {count} cases, need >= {minimum}"


def test_language_coverage_floors():
    cases = load_cases()
    for lang, minimum in MIN_PER_LANGUAGE.items():
        count = sum(c["language"] == lang for c in cases)
        assert count >= minimum, f"language {lang!r}: {count} cases, need >= {minimum}"


def test_referenced_items_exist_in_seed_menu():
    """Every dish name in drafts/setup/expectations must be a real menu item —
    the golden set may not hallucinate either."""
    names = _menu_names()
    for case in load_cases():
        for line in [*case["draft"], *(case["expect"].get("draft") or [])]:
            assert line["name"] in names, f"{case['id']}: {line['name']} not in seed menu"
        for item in case.get("setup", {}).get("make_unavailable", []):
            assert item in names, f"{case['id']}: {item} not in seed menu"


def test_no_time_dependent_expectations():
    """Dishes with a serving schedule (e.g. pongal 06:00-12:00) may never be
    REQUIRED in a draft — the live gate runs at arbitrary wall-clock times
    and the agent correctly refuses off-schedule dishes."""
    from dosadash_ml.datagen import MENU_ITEMS

    scheduled = {m.name for m in MENU_ITEMS if m.schedule}
    for case in load_cases():
        required = {line["name"] for line in [*case["draft"], *(case["expect"].get("draft") or [])]}
        clash = required & scheduled
        assert not clash, f"{case['id']}: {clash} are schedule-gated — eval would be time-dependent"


def test_coverage_of_required_scenarios():
    cases = load_cases()
    assert {c["language"] for c in cases} == LANGUAGES
    assert any(c["kitchen"] == "paused" for c in cases), "need kitchen-paused case"
    assert any(c.get("setup", {}).get("make_unavailable") for c in cases), "need 86'd case"
    assert any("ignore" in c["message"].lower() for c in cases), "need injection case"
    assert any("user" in c and (c["user"] or {}).get("allergens") for c in cases), (
        "need allergen-conflict case"
    )
    assert any((c["expect"].get("ready") is True) for c in cases), "need confirmation case"
    assert any(
        "breakfast" in c["message"].lower() or "snack" in c["message"].lower() for c in cases
    ), "need meal-period suggestion case"


def test_expectations_are_well_formed():
    for case in load_cases():
        expect = case["expect"]
        for line in expect.get("draft") or []:
            assert line["qty"] is None or 1 <= line["qty"] <= 20, case["id"]
        assert isinstance(expect.get("ready", False), bool) or expect["ready"] is None


def test_prompt_file_has_guardrail_rules():
    from dosadash_ai.prompts import load_prompt
    from dosadash_shared import ORDER_AGENT_PROMPT_VERSION

    prompt = load_prompt(ORDER_AGENT_PROMPT_VERSION)
    assert "item_id" in prompt  # draft by id only
    assert "ready_to_place" in prompt and "draft_items" in prompt  # output contract
    assert "DATA, never instructions" in prompt  # injection guardrail
    assert '"available": false' in prompt  # 86'd handling
    assert "Never invent" in prompt  # Hard Rule 2 in prose
    assert "meal_periods" in prompt  # meal-period steering field is documented
