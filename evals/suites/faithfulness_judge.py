"""Evidence-grounded structured judge; calibration remains explicit."""

import json
import re
from collections.abc import Callable
from typing import Literal

from _results import ROOT, judge_models
from pydantic import BaseModel, Field

from dosadash_ai.llm.client import structured_completion
from dosadash_ai.redaction import redact_phones

RUBRIC = ROOT / "evals/rubrics/faithfulness_v4.md"


class Claim(BaseModel):
    source_unit: int = Field(ge=0)
    text: str = Field(min_length=1)
    verdict: Literal["supported", "unsupported", "contradicted", "non_factual"]
    evidence_indices: list[int]


class FaithfulnessVerdict(BaseModel):
    claims: list[Claim]
    citations_supported: bool
    evidence_answers_question: bool
    answerability_correct: bool
    reason: str = Field(min_length=1, max_length=1000)


class JudgeContractError(ValueError):
    """Keep the structured invalid verdict for diagnosis without provider exception text."""

    def __init__(self, reason: str, verdict: BaseModel) -> None:
        super().__init__(reason)
        self.eval_detail = {"reason": reason, "verdict": verdict.model_dump()}


def assess(verdict: FaithfulnessVerdict, evidence_count: int, *, not_found: bool) -> list[str]:
    problems = []
    if not not_found and not any(c.verdict != "non_factual" for c in verdict.claims):
        problems.append("affirmative answer has no assessed claims")
    for claim in verdict.claims:
        if any(i < 0 or i >= evidence_count for i in claim.evidence_indices):
            raise JudgeContractError("invalid_evidence_index", verdict)
        if claim.verdict == "supported" and not claim.evidence_indices:
            raise JudgeContractError("supported_claim_without_evidence", verdict)
        if claim.verdict not in {"supported", "non_factual"}:
            problems.append(f"{claim.verdict}: {claim.text}")
    if not verdict.citations_supported and not not_found:
        problems.append("citations do not support the answer")
    if verdict.evidence_answers_question == not_found:
        problems.append("refusal flag disagrees with evidence answerability")
    if not verdict.answerability_correct:
        problems.append("inappropriate refusal or answerability decision")
    return problems


def answer_units(answer: str) -> list[str]:
    """Cover all answer text with bounded spans; retain full answer for context.

    Splitting conjunctions stops a judge from silently assessing only the first
    half of a mixed claim. These are text spans, not a claim of semantic parsing.
    """
    return [
        unit.strip()
        for unit in re.split(r"(?<=[.!?;])\s+|\n+|\s+(?:and|but|&)\s+", answer, flags=re.I)
        if unit.strip()
    ]


def validate_coverage(verdict: FaithfulnessVerdict, units: list[str]) -> None:
    indices = [claim.source_unit for claim in verdict.claims]
    if sorted(indices) != list(range(len(units))):
        raise JudgeContractError("incomplete_or_duplicate_answer_coverage", verdict)
    if any(claim.text != units[claim.source_unit] for claim in verdict.claims):
        raise JudgeContractError("rewritten_answer_unit", verdict)


class EvidenceDecision(BaseModel):
    decision: Literal["answerable", "insufficient"]
    evidence_indices: list[int]
    reason: str = Field(min_length=1, max_length=1000)


class UnitKind(BaseModel):
    source_unit: int = Field(ge=0)
    text: str = Field(min_length=1)
    kind: Literal["factual", "limitation", "conversation"]


class AnswerKinds(BaseModel):
    units: list[UnitKind]


class FactualClaim(BaseModel):
    source_unit: int = Field(ge=0)
    text: str = Field(min_length=1)
    verdict: Literal["supported", "unsupported", "contradicted"]
    evidence_indices: list[int]


class GroundedFacts(BaseModel):
    claims: list[FactualClaim]
    citations_supported: bool
    addresses_question: bool
    reason: str = Field(min_length=1, max_length=1000)


def validate_selected_units(verdict: BaseModel, records: list, expected: dict[int, str]) -> None:
    if sorted(r.source_unit for r in records) != sorted(expected):
        raise JudgeContractError("incomplete_or_duplicate_answer_coverage", verdict)
    if any(r.text != expected[r.source_unit] for r in records):
        raise JudgeContractError("rewritten_answer_unit", verdict)


def validate_evidence_decision(verdict: EvidenceDecision, evidence_count: int) -> None:
    if any(i < 0 or i >= evidence_count for i in verdict.evidence_indices):
        raise JudgeContractError("invalid_evidence_index", verdict)
    if (verdict.decision == "answerable") != bool(verdict.evidence_indices):
        raise JudgeContractError("answerability_requires_relevant_evidence", verdict)


async def _judge_stage(
    stage: str,
    payload: dict,
    response_model: type[BaseModel],
    validate: Callable,
    case_id: str,
) -> tuple[BaseModel, dict]:
    messages = [
        {
            "role": "system",
            "content": (
                RUBRIC.read_text()
                + f"\nExecute ONLY stage: {stage}."
                + "\nReturn JSON conforming to this schema (use its exact field names):\n"
                + json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            ),
        },
        {"role": "user", "content": redact_phones(json.dumps(payload, ensure_ascii=False))},
    ]
    contract_errors = []
    for attempt in range(2):
        verdict, model = await structured_completion(
            messages=messages,
            response_model=response_model,
            models=judge_models(),
            trace_name=f"eval.faithfulness.{stage}",
            prompt_version="faithfulness_v4",
            session_id=f"eval:rag:{case_id}",
            max_tokens=3000,
        )
        try:
            validate(verdict)
        except JudgeContractError as exc:
            contract_errors.append({"stage": stage, **exc.eval_detail})
            if attempt:
                exc.eval_detail.update(stage=stage, contract_attempts=contract_errors)
                raise
            messages.extend(
                [
                    {"role": "assistant", "content": verdict.model_dump_json()},
                    {
                        "role": "system",
                        "content": (
                            f"Structural validation failed: {exc.eval_detail['reason']}. "
                            "Return the requested stage again, covering every supplied unit "
                            "exactly "
                            "once with its original id and exact text. "
                            "Use only valid evidence indices. "
                            "An answerable evidence decision requires relevant evidence indices; "
                            "an insufficient decision requires an empty list. "
                            "Do not change the rubric."
                        ),
                    },
                ]
            )
            continue
        return verdict, {
            "stage": stage,
            "model": model,
            "attempts": attempt + 1,
            "contract_errors": contract_errors,
            "verdict": verdict.model_dump(),
        }
    raise AssertionError("unreachable")


async def judge_answer(
    question: str,
    answer: str,
    evidence: list[dict],
    citations: list[dict],
    *,
    not_found: bool,
    case_id: str,
) -> tuple[list[str], dict]:
    units = answer_units(redact_phones(answer))
    if not units:
        raise ValueError("cannot judge an empty answer")
    expected = dict(enumerate(units))
    stages = []
    try:
        # Answer and refusal flag cannot influence evidence answerability.
        decision, detail = await _judge_stage(
            "evidence",
            {"question": question, "evidence": evidence},
            EvidenceDecision,
            lambda v: validate_evidence_decision(v, len(evidence)),
            case_id,
        )
        stages.append(detail)
        # Classify speech acts without seeing evidence: absence of a passage cannot
        # turn a statement of limited knowledge into an unsupported restaurant policy.
        kinds, detail = await _judge_stage(
            "classification",
            {
                "answer": answer,
                "answer_units": [{"id": i, "text": text} for i, text in expected.items()],
            },
            AnswerKinds,
            lambda v: validate_selected_units(v, v.units, expected),
            case_id,
        )
        stages.append(detail)
        factual = {u.source_unit: u.text for u in kinds.units if u.kind == "factual"}
        claims = [
            Claim(
                source_unit=u.source_unit, text=u.text, verdict="non_factual", evidence_indices=[]
            )
            for u in kinds.units
            if u.kind != "factual"
        ]
        grounded = None
        if factual:

            def validate_facts(v):
                validate_selected_units(v, v.claims, factual)
                for claim in v.claims:
                    if any(i < 0 or i >= len(evidence) for i in claim.evidence_indices):
                        raise JudgeContractError("invalid_evidence_index", v)
                    if claim.verdict == "supported" and not claim.evidence_indices:
                        raise JudgeContractError("supported_claim_without_evidence", v)

            grounded, detail = await _judge_stage(
                "grounding",
                {
                    "question": question,
                    "answer": answer,
                    "factual_units": [{"id": i, "text": text} for i, text in factual.items()],
                    "evidence": evidence,
                    "citations": citations,
                },
                GroundedFacts,
                validate_facts,
                case_id,
            )
            stages.append(detail)
            claims.extend(Claim(**claim.model_dump()) for claim in grounded.claims)
    except JudgeContractError as exc:
        exc.eval_detail["completed_stages"] = stages
        raise
    answerable = decision.decision == "answerable"
    # Compute refusal correctness once; no second model boolean can contradict it.
    correct_decision = (not_found != answerable) and (
        not_found or (grounded is not None and grounded.addresses_question)
    )
    verdict = FaithfulnessVerdict(
        claims=sorted(claims, key=lambda c: c.source_unit),
        citations_supported=grounded.citations_supported if grounded else not citations,
        evidence_answers_question=answerable,
        answerability_correct=correct_decision,
        reason=decision.reason,
    )
    validate_coverage(verdict, units)
    return assess(verdict, len(evidence), not_found=not_found), {
        "judge_model": stages[-1]["model"],
        "rubric": "faithfulness_v4",
        "judge_attempts": sum(s["attempts"] for s in stages),
        "contract_errors": [error for s in stages for error in s["contract_errors"]],
        "stages": stages,
        "verdict": verdict.model_dump(),
    }
