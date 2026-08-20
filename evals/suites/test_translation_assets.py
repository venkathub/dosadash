"""Key-free CI gates for the menu-translation guardrail (Phase 7, Hard Rule 5).

The LLM only writes text; these gates pin what it can never do: attach a
translation to an item_id that wasn't requested, drop or invent numerals
(pack sizes must survive verbatim), invent prices, or pass off an English
echo as a translation. Omissions must be reported per item — never
fabricated. All checks run against the PRODUCTION sanitizer so the eval
and the serving path can't drift.
"""

import json
import re
from collections import Counter
from pathlib import Path

from dosadash_ai.prompts import load_prompt
from dosadash_ai.routers.translation import build_messages, sanitize_batch
from dosadash_shared import (
    MAX_TRANSLATION_ITEMS,
    MENU_TRANSLATION_PROMPT_VERSION,
    SUPPORTED_TRANSLATION_LANGS,
    TRANSLATION_CHUNK_SIZE,
    TRANSLATION_LANG_NAMES,
    TRANSLATION_SCRIPT_RANGES,
    TranslationDraft,
    TranslationDraftBatch,
    TranslationSourceItem,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "translation_guardrail.jsonl"

_DIGITS = re.compile(r"\d+")


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _run(case: dict):
    items = [TranslationSourceItem.model_validate(i) for i in case["items"]]
    batch = TranslationDraftBatch.model_validate(case["batch"])
    kept, rejected = sanitize_batch(items, batch, case["lang"])
    return items, kept, rejected


def test_golden_dataset_parses_and_is_big_enough():
    cases = _cases()
    assert len(cases) >= 14
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    kinds = {c["kind"] for c in cases}
    # adversarial coverage floor: the gate must keep exercising these
    assert {"hallucination", "omission", "script", "numerals_dropped", "numerals_invented"} <= kinds


def test_guardrail_cases_exact():
    for case in _cases():
        _, kept, rejected = _run(case)
        expect = case["expect"]
        cid = case["id"]
        assert [d.item_id for d in kept] == expect["kept"], cid
        rejected_map = {r.item_id: r.reason for r in rejected}
        assert set(rejected_map) == {int(k) for k in expect["rejected"]}, cid
        for item_id, substring in expect["rejected"].items():
            assert substring in rejected_map[int(item_id)], cid
        by_id = {d.item_id: d for d in kept}
        for item_id, name in expect.get("names", {}).items():
            assert by_id[int(item_id)].name == name, cid
        for item_id, desc in expect.get("descriptions", {}).items():
            assert by_id[int(item_id)].description == desc, cid
        for item_id, label in expect.get("category_labels", {}).items():
            assert by_id[int(item_id)].category_label == label, cid
        for item_id in expect.get("null_description", []):
            assert by_id[item_id].description is None, cid
        for item_id in expect.get("null_category", []):
            assert by_id[item_id].category_label is None, cid


def test_no_kept_draft_ever_changes_numerals():
    """Invariant across ALL cases: whatever survives the guardrail carries
    exactly the source's numerals in the name and invents none anywhere."""
    for case in _cases():
        items, kept, _ = _run(case)
        sources = {i.item_id: i for i in items}
        for draft in kept:
            source = sources[draft.item_id]
            assert Counter(_DIGITS.findall(draft.name)) == Counter(_DIGITS.findall(source.name)), (
                case["id"]
            )
            allowed = Counter(_DIGITS.findall(source.name)) + Counter(
                _DIGITS.findall(source.description or "")
            )
            for text in (draft.description, draft.category_label):
                assert not (Counter(_DIGITS.findall(text or "")) - allowed), case["id"]


def test_kept_names_are_in_target_script():
    for case in _cases():
        _, kept, _ = _run(case)
        low, high = TRANSLATION_SCRIPT_RANGES[case["lang"]]
        for draft in kept:
            assert any(low <= ord(ch) <= high for ch in draft.name), case["id"]


def test_prompt_mentions_every_schema_key():
    prompt = load_prompt(MENU_TRANSLATION_PROMPT_VERSION)
    for key in TranslationDraft.model_fields:
        assert f'"{key}"' in prompt, key
    assert '"translations"' in prompt


def test_build_messages_round_trips_golden_case():
    case = _cases()[0]
    items = [TranslationSourceItem.model_validate(i) for i in case["items"]]
    messages = build_messages(case["lang"], items)
    assert messages[0]["role"] == "system"
    assert "menu localizer" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["target_language"] == TRANSLATION_LANG_NAMES[case["lang"]]
    assert [i["item_id"] for i in payload["items"]] == [i.item_id for i in items]
    assert payload["items"][0]["name"] == items[0].name


def test_language_registry_coherent():
    """Adding a language means adding it EVERYWHERE (name + script range)."""
    for lang in SUPPORTED_TRANSLATION_LANGS:
        assert lang in TRANSLATION_LANG_NAMES
        assert lang in TRANSLATION_SCRIPT_RANGES
        low, high = TRANSLATION_SCRIPT_RANGES[lang]
        assert low < high
    assert 1 <= TRANSLATION_CHUNK_SIZE <= MAX_TRANSLATION_ITEMS


# ------------------------------------------------- agent serving invariants


def _menu_item(aliases: tuple[str, ...] = ()):
    from decimal import Decimal

    from dosadash_ai.agent.context import MenuItemCtx

    return MenuItemCtx(
        id=1,
        name="Masala Dosa",
        category="Dosa",
        price=Decimal("120.00"),
        is_veg=True,
        contains_onion_garlic=True,
        spice_level=1,
        is_available=True,
        schedule=None,
        description=None,
        aliases=aliases,
    )


def test_agent_menu_payload_byte_stable_without_aliases():
    """Prefix-caching + live-gate invariant: an item with no approved
    translations serializes to exactly the pre-localization key set."""
    from dosadash_ai.agent.context import AgentContext, menu_payload

    entry = menu_payload(AgentContext(items={1: _menu_item()}))[0]
    assert set(entry) == {
        "item_id",
        "name",
        "category",
        "price_inr",
        "veg",
        "jain_friendly",
        "spice",
        "allergens",
        "meal_periods",
        "available",
    }


def test_agent_menu_payload_exposes_aliases_without_touching_canon():
    """Aliases let a Tamil order map to the item; the canonical name the
    guardrail and 'my usual' rely on is never replaced."""
    from dosadash_ai.agent.context import AgentContext, menu_payload

    entry = menu_payload(AgentContext(items={1: _menu_item(aliases=("மசாலா தோசை",))}))[0]
    assert entry["aliases"] == ["மசாலா தோசை"]
    assert entry["name"] == "Masala Dosa"
    assert entry["item_id"] == 1
