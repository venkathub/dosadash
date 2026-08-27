"""Feedback triage (Phase 13 slice 3, docs/14) — inventory-agent pattern.

The LLM only OBSERVES (TriageAssessment); `decide()` COMPUTES the verdict
deterministically (dish-QC philosophy: model observes, verdict computed).
Consequences:
- a report can never talk its way into `ai:auto-fix` — the policy demands
  user-filed BUG ∧ model-read BUG ∧ effort S ∧ risk LOW ∧ actionable;
- an LLM chain failure degrades to NEEDS_APPROVAL (a human sees it; a
  report is never lost and never auto-fixed on a guess) — endpoint never
  5xxes for chain failure;
- HIGH risk / non-S effort can never reach AUTO_FIX regardless of what the
  model (or the report text, via injection) claims.
"""

import json
import logging

from dosadash_ai.llm import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_ai.redaction import redact_phones
from dosadash_shared import (
    FEEDBACK_TRIAGE_PROMPT_VERSION,
    LABEL_AI_AUTO_FIX,
    LABEL_AI_NEEDS_APPROVAL,
    LABEL_AI_SPEC,
    FeedbackTriageRequest,
    FeedbackTriageResponse,
    FeedbackType,
    ReporterTier,
    TriageAssessment,
    TriageVerdict,
)

logger = logging.getLogger(__name__)

VERDICT_LABELS: dict[TriageVerdict, list[str]] = {
    TriageVerdict.AUTO_FIX: [LABEL_AI_AUTO_FIX],
    TriageVerdict.NEEDS_APPROVAL: [LABEL_AI_NEEDS_APPROVAL],
    TriageVerdict.DISMISS: [],  # nothing for the fixer to trigger on
}


def needs_spec(
    request: FeedbackTriageRequest,
    assessment: TriageAssessment,
    verdict: TriageVerdict,
) -> bool:
    """S2: should the spec agent draft scope BEFORE the human decides?

    Only for NEEDS_APPROVAL (an auto-fix is small enough not to need one;
    a dismissal has nothing to spec), only for human reporters (SYSTEM
    incidents need investigation, not a feature spec), and only when the
    work is feature-shaped or larger than S. Advisory by construction:
    `ai:spec` dispatches a read-only commenter, never the fixer."""
    if verdict != TriageVerdict.NEEDS_APPROVAL:
        return False
    if request.reporter_tier == ReporterTier.SYSTEM:
        return False
    if not assessment.actionable:
        return False
    return (
        request.type == FeedbackType.FEATURE
        or assessment.type == FeedbackType.FEATURE
        or assessment.effort in ("M", "L")
    )


def labels_for(
    request: FeedbackTriageRequest,
    assessment: TriageAssessment | None,
    verdict: TriageVerdict,
) -> list[str]:
    """Verdict labels + the advisory spec label when earned. The ONLY
    label composer — triage() and the asset gates share it so golden
    expectations can never drift from the shipped composition."""
    labels = list(VERDICT_LABELS[verdict])
    if assessment is not None and needs_spec(request, assessment, verdict):
        labels.append(LABEL_AI_SPEC)
    return labels


def build_messages(request: FeedbackTriageRequest) -> list[dict[str, str]]:
    """Report text re-redacted defensively (Rule 8 — api already redacted)."""
    payload = {
        "report_id": request.report_id,
        "type": request.type,
        "title": redact_phones(request.title),
        "description": redact_phones(request.description),
        "reporter_tier": request.reporter_tier,
    }
    return [
        {"role": "system", "content": load_prompt(FEEDBACK_TRIAGE_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def decide(
    request: FeedbackTriageRequest, assessment: TriageAssessment
) -> tuple[TriageVerdict, list[str]]:
    """Deterministic policy — the ONLY writer of verdicts.

    Returns (verdict, violations). Violations record why an assessment was
    denied AUTO_FIX so the admin tab can show the reasoning.

    S6 capability ladder: `request.max_auto_effort` is a CEILING the api
    computed from measured loop outcomes ("S" structural, "M" earned) —
    the policy never widens it, and L effort can never auto-fix at any
    rung."""
    violations: list[str] = []
    if request.reporter_tier == ReporterTier.SYSTEM:
        # Phase 15 v1 policy: sentinel-filed incidents ALWAYS go to a human
        # — no auto-fix AND no LLM-decided dismissal. Detector precision is
        # measured first (dismissed-rate on the metrics rollup); loosening
        # is a deliberate later step, never a default.
        violations.append("sentinel reports always need approval (v1 policy)")
        return TriageVerdict.NEEDS_APPROVAL, violations
    if not assessment.actionable:
        return TriageVerdict.DISMISS, violations
    if request.type != FeedbackType.BUG:
        violations.append("feature requests always need approval")
        return TriageVerdict.NEEDS_APPROVAL, violations
    if assessment.type != FeedbackType.BUG:
        violations.append("model reads this as a feature disguised as a bug")
        return TriageVerdict.NEEDS_APPROVAL, violations
    allowed_efforts = {"S"} if request.max_auto_effort == "S" else {"S", "M"}
    if assessment.effort not in allowed_efforts:
        violations.append(
            f"effort {assessment.effort} exceeds the current autonomy "
            f"ceiling ({request.max_auto_effort}) — needs a human"
        )
        return TriageVerdict.NEEDS_APPROVAL, violations
    if assessment.risk != "LOW":
        violations.append("HIGH risk never auto-fixes")
        return TriageVerdict.NEEDS_APPROVAL, violations
    return TriageVerdict.AUTO_FIX, violations


async def triage(request: FeedbackTriageRequest) -> FeedbackTriageResponse:
    """LLM assessment → deterministic verdict, with a safe fallback."""
    try:
        assessment, model = await structured_completion(
            messages=build_messages(request),
            response_model=TriageAssessment,
            trace_name="feedback.triage",
            prompt_version=FEEDBACK_TRIAGE_PROMPT_VERSION,
            session_id="feedback:triage",
            max_tokens=400,
        )
    except LLMError as exc:
        logger.warning("feedback triage LLM chain failed for #%s: %s", request.report_id, exc)
        return FeedbackTriageResponse(
            report_id=request.report_id,
            verdict=TriageVerdict.NEEDS_APPROVAL,
            labels=VERDICT_LABELS[TriageVerdict.NEEDS_APPROVAL],
            fallback=True,
            violations=[f"llm unavailable: {exc}"[:300]],
        )
    verdict, violations = decide(request, assessment)
    ladder_level = f"AUTO_FIX_{assessment.effort}" if verdict == TriageVerdict.AUTO_FIX else None
    return FeedbackTriageResponse(
        report_id=request.report_id,
        verdict=verdict,
        assessment=assessment,
        labels=labels_for(request, assessment, verdict),
        violations=violations,
        model=model,
        ladder_level=ladder_level,
    )
