"""Key-free CI gates for dish-photo QC (Phase 7, Hard Rule 5).

The VLM only OBSERVES (dishes seen, visible issues); these gates pin the
deterministic verdict layer: mismatches always outrank cosmetic issues,
bad photos never PASS, unrelated dishes never fuzzy-match, and a total
vision failure is UNREADABLE (retake) rather than a silent pass.
"""

import json
from pathlib import Path

from dosadash_ai.qc.verdict import MATCH_THRESHOLD, compute_result, name_similarity
from dosadash_shared import DISH_QC_PROMPT_VERSION, DishQCExtraction

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "dish_qc.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "dish_qc_v1.md"


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _run(case: dict):
    extraction = (
        DishQCExtraction.model_validate(case["extraction"])
        if case["extraction"] is not None
        else None
    )
    return compute_result(
        case["expected"], extraction, error="chain failed" if extraction is None else None
    )


def test_verdicts_exact():
    """Deterministic layer → exact expectations, zero tolerance."""
    for case in _cases():
        result = _run(case)
        expect = case["expect"]
        assert result.verdict == expect["verdict"], f"{case['id']}: {result.verdict}"
        assert sorted(result.missing) == sorted(expect["missing"]), case["id"]
        assert sorted(result.unexpected) == sorted(expect["unexpected"]), case["id"]


def test_no_bad_photo_ever_passes():
    """Invariant: is_food_photo=false or rock-bottom confidence never PASSes,
    regardless of what dishes the model claims to see."""
    sneaky = DishQCExtraction(
        is_food_photo=False,
        dishes_seen=["masala dosa", "filter coffee"],
        presentation_issues=[],
        confidence=0.99,
    )
    assert compute_result(["Masala Dosa"], sneaky).verdict != "PASS"
    blurry = DishQCExtraction(
        is_food_photo=True, dishes_seen=["masala dosa"], presentation_issues=[], confidence=0.1
    )
    assert compute_result(["Masala Dosa"], blurry).verdict != "PASS"


def test_verdict_coverage():
    kinds = {c["kind"] for c in _cases()}
    verdicts = {c["expect"]["verdict"] for c in _cases()}
    assert {"clean", "mismatch", "issue", "bad_photo", "unreadable", "adversarial"} <= kinds
    assert {"PASS", "MISMATCH", "CHECK", "UNREADABLE"} <= verdicts
    ids = [c["id"] for c in _cases()]
    assert len(ids) == len(set(ids))


def test_similarity_sanity():
    assert name_similarity("Masala Dosa", "masala dosa with chutney") >= MATCH_THRESHOLD
    assert name_similarity("Idli (2 pcs)", "two idlis") >= MATCH_THRESHOLD  # plural/pack-size proof
    assert name_similarity("Sweet Pongal", "semiya payasam") < MATCH_THRESHOLD
    assert name_similarity("", "dosa") == 0.0


def test_prompt_contract_coherence():
    """The prompt must demand observations-only JSON with the schema keys the
    verdict layer consumes, and forbid guessing unseen dishes."""
    prompt = " ".join(PROMPT.read_text().split())  # collapse markdown wrapping
    for key in ("is_food_photo", "dishes_seen", "presentation_issues", "confidence"):
        assert key in prompt, f"prompt missing schema key {key}"
    assert "NEVER list a dish you cannot actually see" in prompt
    assert "pass/fail" in prompt  # tells the model the verdict is not its job
    assert DISH_QC_PROMPT_VERSION == "dish_qc_v1"
    assert PROMPT.stem == DISH_QC_PROMPT_VERSION
