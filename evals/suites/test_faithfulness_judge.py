import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from faithfulness_judge import FaithfulnessVerdict, answer_units, assess, validate_coverage


def verdict(kind="supported", indices=None, citations=True):
    return FaithfulnessVerdict(
        claims=[
            {
                "source_unit": 0,
                "text": "Refunds return to the original payment method.",
                "verdict": kind,
                "evidence_indices": [0] if indices is None else indices,
            }
        ],
        citations_supported=citations,
        evidence_answers_question=True,
        answerability_correct=True,
        reason="Compared the policy passage.",
    )


def test_valid_citation_does_not_make_invented_policy_faithful():
    assert assess(verdict("unsupported", []), 1, not_found=False)
    assert assess(verdict("contradicted"), 1, not_found=False)
    assert assess(verdict(citations=False), 1, not_found=False)
    assert assess(verdict(), 1, not_found=False) == []


def test_invalid_judge_evidence_is_an_error():
    with pytest.raises(ValueError):
        assess(verdict(indices=[9]), 1, not_found=False)
    with pytest.raises(ValueError):
        assess(verdict(indices=[]), 1, not_found=False)


def test_empty_claims_cannot_pass_affirmative_answer():
    v = FaithfulnessVerdict(
        claims=[],
        citations_supported=True,
        evidence_answers_question=False,
        answerability_correct=True,
        reason="No claims",
    )
    assert assess(v, 1, not_found=False)
    assert assess(v, 1, not_found=True) == []


def test_calibration_review_is_scoped_to_owner_approved_examples():
    import hashlib
    import json

    from _results import load_jsonl

    record = json.loads(
        (Path(__file__).resolve().parents[1] / "baselines/p0-sample-review.json").read_text()
    )
    for filename in ("faithfulness_calibration.jsonl", "tone_calibration.jsonl"):
        cases = load_jsonl(filename)
        name = filename.removesuffix("_calibration.jsonl")
        approved = set(record["datasets"][name]["case_ids"])
        assert len(cases) >= 11
        for case in cases:
            assert case["human_reviewed"] == (case["id"] in approved)
            if case["human_reviewed"]:
                assert case["review_record"] == "evals/baselines/p0-sample-review.json"
                content = {
                    key: value
                    for key, value in case.items()
                    if key not in ("human_reviewed", "review_record")
                }
                digest = hashlib.sha256(
                    json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                assert digest == record["datasets"][name]["approved_content_sha256"][case["id"]]


async def test_invalid_judge_verdict_is_preserved_in_failed_case():
    from _results import evaluate_cases

    async def execute(case):
        assess(verdict(indices=[9]), 1, not_found=False)

    result = await evaluate_cases("rag", [{"id": "invalid-judge"}], execute)
    assert result.status == "error" and result.gate_failed
    detail = result.cases[0]["error_detail"]
    assert detail["reason"] == "invalid_evidence_index"
    assert detail["verdict"]["claims"][0]["evidence_indices"] == [9]


def test_mixed_claim_cannot_omit_unsupported_tail():
    units = answer_units("Cancel before cooking and receive a free dessert.")
    assert units == ["Cancel before cooking", "receive a free dessert."]
    v = verdict()
    v.claims[0].text = units[0]
    with pytest.raises(ValueError, match="incomplete_or_duplicate"):
        validate_coverage(v, units)
    v.claims.append(
        v.claims[0].model_copy(
            update={
                "source_unit": 1,
                "text": units[1],
                "verdict": "unsupported",
                "evidence_indices": [],
            }
        )
    )
    validate_coverage(v, units)
    assert assess(v, 1, not_found=False) == ["unsupported: receive a free dessert."]


def test_coverage_rejects_rewritten_or_duplicate_units():
    v = verdict()
    with pytest.raises(ValueError, match="rewritten"):
        validate_coverage(v, ["An entirely different assertion."])
    v.claims.append(v.claims[0].model_copy())
    with pytest.raises(ValueError, match="incomplete_or_duplicate"):
        validate_coverage(v, [v.claims[0].text])


def test_nonfactual_refusal_is_not_an_unsupported_policy():
    v = verdict("non_factual", [])
    v.claims[0].text = "I don't have that information."
    v.evidence_answers_question = False
    assert assess(v, 0, not_found=True) == []
    assert assess(v, 0, not_found=False)  # Cannot disguise an affirmative answer as empty evidence.


def test_refusal_of_answerable_question_fails_even_if_judge_calls_it_correct():
    v = verdict("non_factual", [])
    v.claims[0].text = "I cannot find that information."
    assert v.evidence_answers_question and v.answerability_correct
    assert "refusal flag disagrees with evidence answerability" in assess(v, 1, not_found=True)


async def test_structural_omission_gets_one_repair_but_quality_failure_does_not(monkeypatch):
    import faithfulness_judge as module

    answer = "Refunds return to the original payment method and include a bonus."
    units = answer_units(answer)
    first = verdict()
    first.claims[0].text = units[0]
    repaired = first.model_copy(deep=True)
    repaired.claims.append(
        first.claims[0].model_copy(
            update={
                "source_unit": 1,
                "text": units[1],
                "verdict": "unsupported",
                "evidence_indices": [],
            }
        )
    )
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return (first if len(calls) == 1 else repaired), "judge-model"

    monkeypatch.setattr(module, "structured_completion", fake_completion)
    problems, detail = await module.judge_answer(
        "Refund policy?",
        answer,
        [{"content": "Refunds return to the original payment method."}],
        [],
        not_found=False,
        case_id="repair",
    )
    assert len(calls) == 2
    assert problems == ["unsupported: include a bonus."]
    assert detail["judge_attempts"] == 2 and len(detail["contract_errors"]) == 1
    assert calls[0]["models"] == module.judge_models()


async def test_repeated_structural_error_remains_an_execution_error(monkeypatch):
    import faithfulness_judge as module

    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return verdict(indices=[9]), "judge-model"

    monkeypatch.setattr(module, "structured_completion", fake_completion)
    with pytest.raises(ValueError, match="invalid_evidence_index"):
        await module.judge_answer(
            "Refund policy?",
            verdict().claims[0].text,
            [{"content": "policy"}],
            [],
            not_found=False,
            case_id="invalid",
        )
    assert len(calls) == 2


async def test_calibration_continues_after_error_without_claiming_agreement(monkeypatch):
    import calibrate_judges as module
    from tone_judge_eval import ToneVerdict

    async def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable")

    async def good_tone(*args, **kwargs):
        return ToneVerdict(score=4, reason="Considerate and brief.")

    monkeypatch.setattr(module, "judge_answer", unavailable)
    monkeypatch.setattr(module, "judge_reply", good_tone)
    result = await module.run()
    assert result["errors"] == 11
    assert len(result["cases"]) == 22
    assert result["human_reviewed_cases"] == 22
    assert not result["human_calibration_complete"]
    assert all(not c["agrees"] for c in result["cases"] if c["status"] == "error")


async def test_reviewed_samples_with_judge_errors_are_not_calibrated(monkeypatch):
    import calibrate_judges as module

    monkeypatch.setattr(
        module, "load_jsonl", lambda name: [{"id": "reviewed", "human_reviewed": True}]
    )
    result = await module.run()
    assert result["all_samples_human_reviewed"]
    assert result["errors"] == 2
    assert not result["human_calibration_complete"]
