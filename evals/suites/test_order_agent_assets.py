"""Key-free asset gates for the order-agent golden conversations (CI).

Live order_accuracy scoring lives in order_agent_eval.py; it becomes the
Phase 4 merge gate (fail if < 0.95, Hard Rule 5). These gates keep the
golden set itself coherent with the seed menu and the prompt contract.
"""

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "order_conversations.jsonl"

REQUIRED_FIELDS = {"id", "language", "kitchen", "history", "draft", "message", "expect", "tags"}
LANGUAGES = {"en", "hinglish", "tanglish", "ta"}  # ta = Tamil script (Phase 7 l10n)
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
    "memory",  # Phase 6: "my usual" / episodic recall
    "voice",  # Phase 7: STT-style transcripts (filler words, no punctuation)
    "serving_window",  # Phase 11: dish outside its hard serving window
}
# Phase 4 golden-set coverage floors (docs/05 week 7 deliverable: 150+).
MIN_CASES = 150
MIN_PER_TAG = {
    "typo": 12,
    "adversarial": 12,
    "sold_out": 8,
    "kitchen_paused": 6,
    "allergen": 10,
    "hallucination": 4,
    "confirm": 8,
    "memory": 4,
    "meal_period": 6,
    "voice": 4,  # Phase 7: voice-note ordering transcripts
    "serving_window": 4,  # Phase 11: off-window refusals must stay covered
}
MIN_PER_LANGUAGE = {"en": 60, "hinglish": 20, "tanglish": 20, "ta": 10}


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
    """Phase 11: every dish carries a serving window, and the live gate pins
    the availability clock to _harness.EVAL_CLOCK_IST. Deterministic rule:
    any dish a case REQUIRES (input draft, expected draft, or seeded usual)
    must be ON schedule at the pinned instant; serving_window cases must
    forbid a dish that is OFF at the pinned instant."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from _harness import EVAL_CLOCK_IST

    from dosadash_ml.datagen import MENU_ITEMS
    from dosadash_shared.availability import item_on_schedule

    pinned = datetime.fromisoformat(EVAL_CLOCK_IST).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    on_schedule = {m.name: item_on_schedule(m.schedule, pinned) for m in MENU_ITEMS}
    for case in load_cases():
        required = {line["name"] for line in [*case["draft"], *(case["expect"].get("draft") or [])]}
        usual = case.get("setup", {}).get("seed_usual_orders", {})
        if isinstance(usual, dict):
            required |= {line["name"] for line in usual.get("items", [])}
        off = {name for name in required if not on_schedule.get(name, True)}
        assert not off, (
            f"{case['id']}: {off} are off-schedule at the pinned eval clock "
            f"({EVAL_CLOCK_IST} IST) — the agent would correctly refuse them"
        )
        if "serving_window" in case["tags"]:
            forbidden = set(case["expect"].get("forbid_names", []))
            off_window_forbidden = {n for n in forbidden if n in on_schedule and not on_schedule[n]}
            assert off_window_forbidden, (
                f"{case['id']}: serving_window case must forbid a dish that is "
                f"off-schedule at {EVAL_CLOCK_IST} IST"
            )


def test_tamil_cases_seed_the_aliases_they_rely_on():
    """Phase 7 l10n: agent aliases exist only for APPROVED translations, and
    the harness seeds them per case — a Tamil-script case without seeded
    translations would test the agent blind. Seeds must reference real menu
    items, supported languages, and text actually in the target script
    (same invariants the translation guardrail enforces in production)."""
    from dosadash_shared import SUPPORTED_TRANSLATION_LANGS, TRANSLATION_SCRIPT_RANGES

    names = _menu_names()
    for case in load_cases():
        seeds = case.get("setup", {}).get("seed_translations", [])
        if case["language"] == "ta":
            assert seeds, f"{case['id']}: ta case must seed_translations (agent sees no alias)"
        for seed in seeds:
            assert seed["name"] in names, f"{case['id']}: {seed['name']} not in seed menu"
            assert seed["lang"] in SUPPORTED_TRANSLATION_LANGS, case["id"]
            low, high = TRANSLATION_SCRIPT_RANGES[seed["lang"]]
            assert any(low <= ord(ch) <= high for ch in seed["text"]), (
                f"{case['id']}: seed text for {seed['name']} not in target script"
            )


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
    # Phase 11 contract: the model sees orderable dishes only and NEVER
    # narrates serving hours itself — the deterministic post-pass does
    assert "being served right now" in prompt
    assert "automatically appends" in prompt and "never guess hours" in prompt
    assert '"not_serving_now"' not in prompt  # retired — it poisoned the model
    assert "Never invent" in prompt  # Hard Rule 2 in prose
    assert '"good_for"' not in prompt and "meal_periods" not in prompt  # field retired in v5
