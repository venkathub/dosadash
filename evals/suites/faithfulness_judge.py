"""Evidence-grounded structured judge; calibration remains explicit."""

import json
import re
from typing import Literal

from _results import ROOT, judge_models
from pydantic import BaseModel, Field

from dosadash_ai.llm.client import structured_completion
from dosadash_ai.redaction import redact_phones

RUBRIC = ROOT / "evals/rubrics/faithfulness_v3.md"


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

    def __init__(self, reason: str, verdict: FaithfulnessVerdict) -> None:
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
    payload = {
        "answer_units": [{"id": i, "text": text} for i, text in enumerate(units)],
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "citations": citations,
        "not_found": not_found,
    }
    messages = [
        {"role": "system", "content": RUBRIC.read_text()},
        {"role": "user", "content": redact_phones(json.dumps(payload, ensure_ascii=False))},
    ]
    contract_errors = []
    for attempt in range(2):
        verdict, model = await structured_completion(
            messages=messages,
            response_model=FaithfulnessVerdict,
            models=judge_models(),
            trace_name="eval.faithfulness",
            prompt_version="faithfulness_v3",
            session_id=f"eval:rag:{case_id}",
            max_tokens=3000,
        )
        try:
            validate_coverage(verdict, units)
            problems = assess(verdict, len(evidence), not_found=not_found)
        except JudgeContractError as exc:
            contract_errors.append(exc.eval_detail)
            if attempt:
                exc.eval_detail = {**exc.eval_detail, "contract_attempts": contract_errors}
                raise
            messages.extend(
                [
                    {"role": "assistant", "content": verdict.model_dump_json()},
                    {
                        "role": "system",
                        "content": (
                            f"Structural validation failed: {exc.eval_detail['reason']}. "
                            "Return exactly one claim for each source_unit in "
                            f"{list(range(len(units)))}. "
                            "Include non_factual units too; copy each unit text exactly "
                            "and use only "
                            "valid evidence indices. Reassess every unit; do not change the rubric."
                        ),
                    },
                ]
            )
            continue
        return problems, {
            "judge_model": model,
            "rubric": "faithfulness_v3",
            "judge_attempts": attempt + 1,
            "contract_errors": contract_errors,
            "verdict": verdict.model_dump(),
        }
    raise AssertionError("unreachable")
