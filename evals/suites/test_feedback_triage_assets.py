"""Key-free asset gates for feedback triage (Phase 13 + 15, docs/14+15).

Replays golden LLM assessments through the REAL deterministic policy
(`feedback_triage.decide`) and asserts the invariants that make the
self-healing loop safe to automate:
- HIGH risk / non-S effort / any FEATURE can NEVER reach AUTO_FIX;
- SYSTEM (sentinel) reports can NEVER auto-fix AND never silently dismiss
  (Phase 15 v1 policy — every machine-filed incident reaches a human);
- DISMISS emits nothing the fixer workflow can trigger on;
- emitted labels stay inside the shared registry;
- the prompt and the policy cannot drift apart silently;
- planted PII never reaches the LLM messages (Hard Rule 8);
- the static system prompt clears the provider prompt-caching minimum
  (S7 — the volatile report rides the user message, never the prefix).
"""

import itertools
import json
from pathlib import Path

from dosadash_ai import feedback_triage
from dosadash_ai.feedback_triage import (
    VERDICT_LABELS,
    build_messages,
    decide,
    labels_for,
    needs_spec,
)
from dosadash_shared import (
    FEEDBACK_TRIAGE_PROMPT_VERSION,
    FIXER_TRIGGER_LABELS,
    GITHUB_LABELS,
    LABEL_AI_AUTO_FIX,
    FeedbackTriageRequest,
    ReporterTier,
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
    # the SHIPPED label composition (verdict labels + advisory ai:spec)
    return verdict, labels_for(request, assessment, verdict), violations


# ---------------------------------------------------------------- golden set


def test_golden_set_shape() -> None:
    cases = _cases()
    assert len(cases) >= 17
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    adversarial = [c for c in cases if c["kind"] == "adversarial"]
    assert len(adversarial) >= 6, "adversarial coverage floor"
    assert any(c["kind"] == "fallback" for c in cases), "chain-failure case required"
    # Phase 15: sentinel coverage floor — incl. an adversarial case where
    # injected evidence text fools the model into an S/LOW read.
    system_cases = [c for c in cases if c["request"]["reporter_tier"] == "SYSTEM"]
    assert len(system_cases) >= 3, "SYSTEM-tier coverage floor"
    assert any(c["kind"] == "adversarial" for c in system_cases)


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
    """Property sweep over the full assessment × tier × ladder space: the
    only path to AUTO_FIX is BUG-filed ∧ BUG-read ∧ effort within the
    caller's ladder ceiling ∧ LOW ∧ actionable ∧ a HUMAN reporter tier."""
    for filed in ["BUG", "FEATURE"]:
        for tier in list(ReporterTier):
            for ceiling in ["S", "M"]:
                request = FeedbackTriageRequest(
                    report_id=1,
                    type=filed,
                    title="sweep",
                    description="sweep",
                    reporter_tier=tier,
                    max_auto_effort=ceiling,
                )
                for assessment in _all_assessments():
                    verdict, _ = decide(request, assessment)
                    if verdict == TriageVerdict.AUTO_FIX:
                        assert filed == "BUG"
                        assert assessment.type == "BUG"
                        allowed = {"S"} if ceiling == "S" else {"S", "M"}
                        assert assessment.effort in allowed
                        assert assessment.effort != "L", "L never auto-fixes at any rung"
                        assert assessment.risk == "LOW"
                        assert assessment.actionable
                        assert tier != ReporterTier.SYSTEM


def test_ladder_ceiling_is_never_widened() -> None:
    """S6: the ceiling is the api's EARNED autonomy state — the policy may
    only narrow it. Default requests (no ceiling field) stay S-only, so an
    old caller can never accidentally grant M."""
    assessment_m = TriageAssessment(
        actionable=True, type="BUG", severity="LOW", effort="M", risk="LOW", summary="sweep"
    )
    base = {"report_id": 1, "type": "BUG", "title": "sweep", "description": "sweep"}
    default_request = FeedbackTriageRequest(**base, reporter_tier="CUSTOMER")
    assert default_request.max_auto_effort == "S", "ladder default must be the bottom rung"
    verdict, violations = decide(default_request, assessment_m)
    assert verdict == TriageVerdict.NEEDS_APPROVAL
    assert violations

    m_request = FeedbackTriageRequest(**base, reporter_tier="CUSTOMER", max_auto_effort="M")
    verdict, _ = decide(m_request, assessment_m)
    assert verdict == TriageVerdict.AUTO_FIX


def test_system_tier_always_needs_approval() -> None:
    """Phase 15 v1 policy: EVERY sentinel report reaches a human — no
    auto-fix, and no LLM-decided dismissal either (a fooled or degraded
    model must not be able to silence a production incident)."""
    request = FeedbackTriageRequest(
        report_id=1,
        type="BUG",
        title="sentinel sweep",
        description="sweep",
        reporter_tier=ReporterTier.SYSTEM,
    )
    for assessment in _all_assessments():
        verdict, violations = decide(request, assessment)
        assert verdict == TriageVerdict.NEEDS_APPROVAL
        assert violations, "SYSTEM routing must be explained to the admin tab"


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
    for token in ["S", "M", "L", "LOW", "HIGH", "BUG", "FEATURE", "SYSTEM"]:
        assert token in text
    # injection hardening + observe-only stance must be stated
    assert "data" in text.lower()
    assert "never decide" in text.lower() or "never change" in text.lower()
    assert FEEDBACK_TRIAGE_PROMPT_VERSION == "feedback_triage_v2"


def test_prompt_static_prefix_is_cacheable() -> None:
    """S7 (docs/15): the system prompt is the whole stable prefix (the
    volatile report is the user message). OpenAI's automatic prompt cache
    needs ≥1,024 tokens of stable prefix — below that every triage call
    pays full input price. chars/4 is the conservative token heuristic."""
    text = PROMPT.read_text()
    assert len(text) // 4 >= 1024, (
        f"triage system prompt ~{len(text) // 4} tokens — below the 1,024-token "
        "provider caching minimum; pad with few-shot examples, don't shrink"
    )


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


# ------------------------------------------------- spec lane (Phase 15 S2)


def test_spec_label_is_advisory_and_scoped() -> None:
    """Property sweep: ai:spec accompanies ONLY human-filed NEEDS_APPROVAL
    verdicts on actionable feature-shaped or M/L work — and it can never
    dispatch the fixer (not a trigger label, structurally)."""
    from dosadash_shared import LABEL_AI_SPEC

    assert LABEL_AI_SPEC not in FIXER_TRIGGER_LABELS, "spec must never trigger the fixer"
    assert LABEL_AI_SPEC in GITHUB_LABELS

    for filed in ["BUG", "FEATURE"]:
        for tier in list(ReporterTier):
            for ceiling in ["S", "M"]:
                request = FeedbackTriageRequest(
                    report_id=1,
                    type=filed,
                    title="sweep",
                    description="sweep",
                    reporter_tier=tier,
                    max_auto_effort=ceiling,
                )
                for assessment in _all_assessments():
                    verdict, _ = decide(request, assessment)
                    labels = labels_for(request, assessment, verdict)
                    if LABEL_AI_SPEC in labels:
                        assert verdict == TriageVerdict.NEEDS_APPROVAL
                        assert tier != ReporterTier.SYSTEM
                        assert assessment.actionable
                        assert (
                            filed == "FEATURE"
                            or assessment.type == "FEATURE"
                            or assessment.effort in ("M", "L")
                        )
                    if verdict != TriageVerdict.NEEDS_APPROVAL:
                        assert LABEL_AI_SPEC not in labels
                    assert needs_spec(request, assessment, verdict) == (
                        LABEL_AI_SPEC in labels
                    ), "composer and predicate must agree"
