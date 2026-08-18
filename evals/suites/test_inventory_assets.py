"""Key-free CI gates for the Phase 6 inventory agent (Hard Rule 5).

Pins the PO guardrail against the adversarial golden set: zero hallucinated
ingredients survive, quantities always land in [deficit, 3×deficit], every
short ingredient is covered, and grouping always follows the canonical
supplier — regardless of what the "LLM" (golden batch) proposed. Also keeps
the prompt's stated rules coherent with the enforced constants.
"""

import json
from decimal import Decimal
from pathlib import Path

from dosadash_ai.inventory.guardrail import MAX_ORDER_FACTOR, group_by_supplier, sanitize_batch
from dosadash_shared import IngredientNeed, PODraftBatch

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "inventory_po.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "inventory_agent_v1.md"

VIOLATION_KINDS = ("unknown", "duplicate", "raised", "capped", "omitted")


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _needs(case: dict) -> dict[int, IngredientNeed]:
    needs = {}
    for row in case["needs"]:
        deficit = Decimal(row["deficit"])
        needs[row["id"]] = IngredientNeed(
            ingredient_id=row["id"],
            name=row["name"],
            unit=row["unit"],
            stock_qty=Decimal("1"),
            reorder_point=Decimal("0"),
            need_qty=deficit + Decimal("1"),
            deficit_qty=deficit,
            supplier_id=row["supplier_id"],
        )
    return needs


def _run(case: dict):
    needs = _needs(case)
    batch = PODraftBatch.model_validate(case["batch"])
    lines, rationale, violations = sanitize_batch(batch, needs)
    drafts = group_by_supplier(lines, needs, rationale=rationale)
    return needs, lines, violations, drafts


def test_golden_set_shape():
    cases = _cases()
    assert len(cases) >= 12
    assert len({c["id"] for c in cases}) == len(cases)
    adversarial = [c for c in cases if c["kind"] == "adversarial"]
    assert len(adversarial) >= 6  # abuse coverage floor
    for case in cases:
        assert set(case["expect"].get("violation_kinds", [])) <= set(VIOLATION_KINDS)


def test_zero_hallucinated_ingredients_pass():
    for case in _cases():
        needs, lines, _, _ = _run(case)
        rogue = [ln.ingredient_id for ln in lines if ln.ingredient_id not in needs]
        assert not rogue, f"{case['id']}: hallucinated ingredients ordered: {rogue}"


def test_quantities_always_inside_band_and_every_need_covered():
    for case in _cases():
        needs, lines, _, _ = _run(case)
        by_id = {ln.ingredient_id: ln.qty for ln in lines}
        assert set(by_id) == set(needs), f"{case['id']}: coverage mismatch"
        for ingredient_id, qty in by_id.items():
            deficit = needs[ingredient_id].deficit_qty
            assert deficit <= qty <= deficit * MAX_ORDER_FACTOR, (
                f"{case['id']}: qty {qty} outside band for ingredient {ingredient_id}"
            )


def test_expected_quantities_and_violations():
    for case in _cases():
        _, lines, violations, _ = _run(case)
        expected_qty = {int(k): Decimal(v) for k, v in case["expect"]["qty"].items()}
        actual_qty = {ln.ingredient_id: ln.qty for ln in lines}
        assert actual_qty == expected_qty, f"{case['id']}: {actual_qty} != {expected_qty}"

        expected_kinds = set(case["expect"].get("violation_kinds", []))
        actual_kinds = {kind for kind in VIOLATION_KINDS if any(kind in v for v in violations)}
        assert actual_kinds == expected_kinds, (
            f"{case['id']}: violation kinds {actual_kinds} != {expected_kinds} ({violations})"
        )


def test_grouping_follows_canonical_supplier():
    for case in _cases():
        needs, _, _, drafts = _run(case)
        for draft in drafts:
            for line in draft.lines:
                assert needs[line.ingredient_id].supplier_id == draft.supplier_id, (
                    f"{case['id']}: ingredient {line.ingredient_id} grouped under "
                    f"supplier {draft.supplier_id}"
                )
        expected_groups = case["expect"].get("groups")
        if expected_groups:
            actual = {
                "null" if d.supplier_id is None else str(d.supplier_id): sorted(
                    ln.ingredient_id for ln in d.lines
                )
                for d in drafts
            }
            assert actual == {k: sorted(v) for k, v in expected_groups.items()}, case["id"]


def test_prompt_matches_guardrail_constants():
    prompt = PROMPT.read_text()
    assert "3 ×" in prompt or "3x" in prompt.lower(), "prompt must state the 3× cap"
    assert MAX_ORDER_FACTOR == Decimal("3"), "cap changed — update prompt + golden set"
    assert "deficit" in prompt
    for field in ("drafts", "supplier_id", "rationale", "lines", "ingredient_id", "qty", "reason"):
        assert field in prompt, f"prompt missing output field {field}"
    assert "Never invent" in prompt  # hallucination rule stated to the model
