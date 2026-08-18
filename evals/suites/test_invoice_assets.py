"""Key-free CI gates for supplier invoice OCR (Phase 6, Hard Rule 5).

The VLM only reads photos; these gates pin the deterministic layers that
decide what happens next: the arithmetic verifier (model can't assert
confidence it didn't earn) and the invoice↔PO matcher (short deliveries,
padded bills and wrong-supplier invoices must always be flagged).
"""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from dosadash_ai.invoice.verify import verify
from dosadash_api.services.invoice_service import match_against_po
from dosadash_shared import INVOICE_AUTO_MATCH_THRESHOLD, InvoiceExtraction

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "invoice_match.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "invoice_extract_v1.md"


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _fake_po(spec: dict) -> SimpleNamespace:
    """Attribute stand-in for a PurchaseOrder row (matcher reads attrs only)."""
    return SimpleNamespace(
        id=1,
        supplier=SimpleNamespace(name=spec["supplier"]),
        items=[
            SimpleNamespace(
                ingredient_id=item["ingredient_id"],
                ingredient=SimpleNamespace(name=item["name"]),
                qty=Decimal(item["qty"]),
            )
            for item in spec["items"]
        ],
    )


def test_golden_set_shape():
    cases = _cases()
    assert len(cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)
    discrepancies = [c for c in cases if c["kind"] in ("discrepancy", "arithmetic")]
    assert len(discrepancies) >= 6  # fraud/short-delivery coverage floor


def test_matcher_verdicts():
    for case in (c for c in _cases() if "po" in c):
        extraction = InvoiceExtraction.model_validate(case["extraction"])
        match = match_against_po(extraction, _fake_po(case["po"]))
        expect = case["expect"]

        if "min_score" in expect:
            assert match.score >= expect["min_score"], f"{case['id']}: score {match.score}"
        if "max_score" in expect:
            assert match.score <= expect["max_score"], f"{case['id']}: score {match.score}"

        by_id = {m.po_ingredient_id: m for m in match.line_matches}
        for ingredient_id, ok in expect.get("qty_ok", {}).items():
            assert by_id[int(ingredient_id)].qty_ok is ok, f"{case['id']}: qty_ok[{ingredient_id}]"
        assert sorted(match.extra_invoice_lines) == sorted(expect.get("extras", [])), case["id"]
        missing = [m.po_ingredient_id for m in match.line_matches if m.invoice_name is None]
        assert sorted(missing) == sorted(expect.get("missing", [])), case["id"]


def test_arithmetic_verdicts():
    for case in (c for c in _cases() if c["kind"] == "arithmetic"):
        extraction = InvoiceExtraction.model_validate(case["extraction"])
        _, arithmetic_ok, confidence = verify(extraction)
        expect = case["expect"]
        assert arithmetic_ok is expect["arithmetic_ok"], f"{case['id']}: arithmetic_ok"
        if "min_confidence" in expect:
            assert confidence >= expect["min_confidence"], f"{case['id']}: {confidence}"
        if "max_confidence" in expect:
            assert confidence <= expect["max_confidence"], f"{case['id']}: {confidence}"


def test_no_discrepancy_case_clears_the_auto_match_gate():
    """A flagged invoice must never look pre-checked: every discrepancy case,
    combined with even perfect extraction confidence, stays below the gate."""
    for case in (c for c in _cases() if c["kind"] == "discrepancy"):
        extraction = InvoiceExtraction.model_validate(case["extraction"])
        match = match_against_po(extraction, _fake_po(case["po"]))
        combined = 0.5 * 1.0 + 0.5 * match.score  # perfect extraction half
        if case["expect"].get("max_score", 1.0) <= 0.6:
            assert combined < INVOICE_AUTO_MATCH_THRESHOLD, f"{case['id']}: {combined}"


def test_prompt_forbids_invention():
    prompt = PROMPT.read_text()
    assert "NEVER" in prompt and "null" in prompt  # no-guessing rule stated
    for field in ("supplier_name", "invoice_number", "lines", "qty", "unit_price", "total"):
        assert field in prompt, f"prompt missing output field {field}"
