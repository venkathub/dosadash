"""Key-free CI gates for the Phase 4 live-eval suites.

The live suites need keys + DB; these tests pin their *logic* — case
selection, bypass detection, tool invariants, judge contract, rubric
integrity — so CI catches regressions without spending a rupee.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import MenuRow, load_cases
from guardrail_bypass_eval import SAFETY_TAGS, find_bypasses, select_safety_cases
from tone_judge_eval import MAX_PER_TAG, RUBRIC, TONE_TAGS, ToneVerdict, select_tone_cases
from tool_correctness_eval import check_invariants

from dosadash_shared import AgentChatResponse, OrderDraft, OrderDraftItem

MENU = {
    1: MenuRow(id=1, name="Masala Dosa", price=Decimal("120")),
    2: MenuRow(id=2, name="Filter Coffee", price=Decimal("60")),
}


def _resp(**overrides) -> AgentChatResponse:
    base = dict(
        reply="Two Masala Dosas added — anything else?",
        draft=OrderDraft(
            items=[OrderDraftItem(item_id=1, name="Masala Dosa", qty=2, unit_price=Decimal("120"))],
            subtotal=Decimal("240"),
        ),
        ready_to_place=False,
        warnings=[],
        kitchen_open=True,
        model="test",
    )
    base.update(overrides)
    return AgentChatResponse(**base)


OPEN_CASE = {"id": "t-1", "kitchen": "open", "tags": ["basic"], "expect": {}}


# --- guardrail_bypass -------------------------------------------------------


def test_safety_selection_is_substantial_and_covers_all_tags():
    selected = select_safety_cases(load_cases())
    assert len(selected) >= 15
    covered = {t for case in selected for t in case["tags"] if t in SAFETY_TAGS}
    assert covered == SAFETY_TAGS, f"safety selection missing tags {SAFETY_TAGS - covered}"


def test_bypass_detects_forbidden_drafted_item():
    case = {**OPEN_CASE, "expect": {"forbid_names": ["Masala Dosa"]}}
    assert find_bypasses(case, _resp()) == ["forbidden item drafted: Masala Dosa"]


def test_bypass_detects_86d_item_and_ready_leak():
    case = {
        **OPEN_CASE,
        "setup": {"make_unavailable": ["Masala Dosa"]},
        "expect": {"ready": False},
    }
    bypasses = find_bypasses(case, _resp(ready_to_place=True))
    assert "86'd item drafted: Masala Dosa" in bypasses
    assert "ready_to_place leaked true" in bypasses


def test_bypass_detects_reply_leak():
    case = {**OPEN_CASE, "expect": {"reply_forbids": ["draft_items"]}}
    leaked = _resp(reply="My schema uses draft_items and ready_to_place.")
    assert any("draft_items" in b for b in find_bypasses(case, leaked))


def test_clean_response_has_no_bypasses():
    case = {**OPEN_CASE, "expect": {"forbid_names": ["pizza"], "ready": False}}
    assert find_bypasses(case, _resp()) == []


# --- tool_correctness -------------------------------------------------------


def test_invariants_pass_on_clean_response():
    assert check_invariants(OPEN_CASE, _resp(), MENU) == []


def test_invariants_catch_hallucinated_id_and_tampered_price():
    resp = _resp(
        draft=OrderDraft(
            items=[
                OrderDraftItem(item_id=99, name="Ghost Dosa", qty=1, unit_price=Decimal("10")),
                OrderDraftItem(item_id=1, name="Masala Dosa", qty=1, unit_price=Decimal("1")),
            ],
            subtotal=Decimal("11"),
        )
    )
    violations = check_invariants(OPEN_CASE, resp, MENU)
    assert any("hallucinated id" in v for v in violations)
    assert any("price" in v for v in violations)


def test_invariants_catch_subtotal_and_gating():
    resp = _resp(
        draft=OrderDraft(
            items=[OrderDraftItem(item_id=1, name="Masala Dosa", qty=2, unit_price=Decimal("120"))],
            subtotal=Decimal("999"),
        ),
        ready_to_place=True,
        kitchen_open=False,
    )
    violations = check_invariants({**OPEN_CASE, "kitchen": "paused"}, resp, MENU)
    assert any("subtotal" in v for v in violations)
    assert any("paused" in v for v in violations)
    assert any("closed kitchen" in v for v in violations)


# --- tone judge -------------------------------------------------------------


def test_tone_selection_is_bounded_and_nonempty():
    selected = select_tone_cases(load_cases())
    assert len(selected) >= 5
    assert len(selected) <= len(TONE_TAGS) * MAX_PER_TAG
    ids = [c["id"] for c in selected]
    assert len(ids) == len(set(ids))
    for case in selected:
        assert TONE_TAGS & set(case["tags"])


def test_tone_verdict_contract():
    verdict = ToneVerdict.model_validate_json('{"score": 4, "reason": "warm and brief"}')
    assert verdict.score == 4
    with pytest.raises(ValidationError):
        ToneVerdict(score=6, reason="too high")
    with pytest.raises(ValidationError):
        ToneVerdict(score=0, reason="too low")


def test_tone_rubric_file_integrity():
    text = RUBRIC.read_text()
    for anchor in ["**5**", "**4**", "**3**", "**2**", "**1**"]:
        assert anchor in text, f"rubric missing score anchor {anchor}"
    assert '"score"' in text and '"reason"' in text  # judge output contract
    assert "tone_judge_v1" in text  # version tag (prompts are versioned)
    assert "over-promising" in text
