"""Key-free asset gates for feedback triage (Phase 13, docs/14).

Replays golden LLM assessments through the REAL deterministic policy
(`feedback_triage.decide`) and asserts the invariants that make the
self-healing loop safe to automate:
- HIGH risk / non-S effort / any FEATURE can NEVER reach AUTO_FIX;
- DISMISS emits nothing the fixer workflow can trigger on;
- emitted labels stay inside the shared registry;
- the prompt and the policy cannot drift apart silently;
- planted PII never reaches the LLM messages (Hard Rule 8).
"""

import itertools
import json
from pathlib import Path

from dosadash_ai import feedback_triage
from dosadash_ai.feedback_triage import VERDICT_LABELS, build_messages, decide
from dosadash_shared import (
    FEEDBACK_TRIAGE_PROMPT_VERSION,
    FIXER_TRIGGER_LABELS,
    GITHUB_LABELS,
    LABEL_AI_AUTO_FIX,
    FeedbackTriageRequest,
    TriageAssessment,
    TriageVerdict,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "feedback_triage.jsonl"
PROMPT = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "ai"
    / "prompts"
    / f"{FEEDBACK_TRIAGE_PROMPT_VERSION}.md"
)


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _run(case: dict) -> tuple[TriageVerdict, list[str], list[str]]:
    request = FeedbackTriageRequest.model_validate(case["request"])
    if case["assessment"] is None:
        # the documented LLM-chain-failure fallback (feedback_triage.triage)
        verdict = TriageVerdict.NEEDS_APPROVAL
        return verdict, VERDICT_LABELS[verdict], ["llm unavailable"]
    assessment = TriageAssessment.model_validate(case["assessment"])
    verdict, violations = decide(request, assessment)
    return verdict, VERDICT_LABELS[verdict], violations


# ---------------------------------------------------------------- golden set


def test_golden_set_shape() -> None:
    cases = _cases()
    assert len(cases) >= 14
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    adversarial = [c for c in cases if c["kind"] == "adversarial"]
    assert len(adversarial) >= 6, "adversarial coverage floor"
    assert any(c["kind"] == "fallback" for c in cases), "chain-failure case required"


def test_golden_expectations() -> None:
    for case in _cases():
        verdict, labels, violations = _run(case)
        expect = case["expect"]
        assert verdict == expect["verdict"], f"{case['id']}: {verdict} != {expect['verdict']}"
        assert labels == expect["labels"], f"{case['id']}: labels {labels}"
        assert bool(violations) == expect["violations"], f"{case['id']}: violations {violations}"


# ---------------------------------------------------------------- invariants


def _all_assessments():
    for actionable, type_, severity, effort, risk in itertools.product(
        [True, False],
        ["BUG", "FEATURE"],
        ["LOW", "MEDIUM", "HIGH"],
        ["S", "M", "L"],
        ["LOW", "HIGH"],
    ):
        yield TriageAssessment(
            actionable=actionable,
            type=type_,
            severity=severity,
            effort=effort,
            risk=risk,
            summary="sweep",
        )


def test_high_risk_can_never_auto_fix() -> None:
    """Property sweep over the full assessment space: the only path to
    AUTO_FIX is BUG-filed ∧ BUG-read ∧ S ∧ LOW ∧ actionable."""
    for filed in ["BUG", "FEATURE"]:
        request = FeedbackTriageRequest(
            report_id=1, type=filed, title="sweep", description="sweep", reporter_tier="ANON"
        )
        for assessment in _all_assessments():
            verdict, _ = decide(request, assessment)
            if verdict == TriageVerdict.AUTO_FIX:
                assert filed == "BUG"
                assert assessment.type == "BUG"
                assert assessment.effort == "S"
                assert assessment.risk == "LOW"
                assert assessment.actionable


def test_dismiss_emits_no_fixer_trigger() -> None:
    assert VERDICT_LABELS[TriageVerdict.DISMISS] == []
    for verdict, labels in VERDICT_LABELS.items():
        for label in labels:
            assert label in GITHUB_LABELS, f"{verdict}: {label} not in registry"
    # the ONLY triage label the fixer may auto-run on
    assert set(VERDICT_LABELS[TriageVerdict.AUTO_FIX]) == {LABEL_AI_AUTO_FIX}
    assert LABEL_AI_AUTO_FIX in FIXER_TRIGGER_LABELS


# ---------------------------------------------------------------- coherence


def test_prompt_matches_policy_constants() -> None:
    """A policy change must force a prompt (and golden) update."""
    text = PROMPT.read_text()
    for field in ["actionable", "type", "severity", "effort", "risk", "area", "summary"]:
        assert f"`{field}`" in text, f"prompt must document output field {field}"
    for token in ["S", "M", "L", "LOW", "HIGH", "BUG", "FEATURE"]:
        assert token in text
    # injection hardening + observe-only stance must be stated
    assert "data" in text.lower()
    assert "never decide" in text.lower() or "never change" in text.lower()
    assert FEEDBACK_TRIAGE_PROMPT_VERSION == "feedback_triage_v1"


def test_planted_phone_never_reaches_llm_messages() -> None:
    request = FeedbackTriageRequest(
        report_id=99,
        type="BUG",
        title="OTP broken, call +91 98765 43210",
        description="Ring me back on 09876543210 and fix login.",
        reporter_tier="CUSTOMER",
    )
    for message in build_messages(request):
        assert "98765" not in message["content"]
        assert "09876543210" not in message["content"]


def test_fallback_constants_are_safe() -> None:
    """The chain-failure path must never be able to auto-fix."""
    assert VERDICT_LABELS[TriageVerdict.NEEDS_APPROVAL] != VERDICT_LABELS[TriageVerdict.AUTO_FIX]
    assert feedback_triage.VERDICT_LABELS[TriageVerdict.NEEDS_APPROVAL] == ["ai:needs-approval"]
