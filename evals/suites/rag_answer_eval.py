"""Live RAG answer eval — grounding, citations, refusals, redaction.

Requires real LLM keys AND an ingested database:

    uv run python -m dosadash_ai.rag.ingest --knowledge-dir knowledge
    uv run python evals/suites/rag_answer_eval.py

Run by the combined CI runner. Quality thresholds and execution errors block
the combined gate after owner-reviewed calibration.

Thresholds are configured in evals/config.json.
"""

import asyncio
import sys

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.rag.answers import answer_question


def load_cases() -> list[dict]:
    from _results import load_jsonl

    return load_jsonl("rag_answers.jsonl")


def score_case(case: dict, answer: str, citation_docs: list[str], not_found: bool) -> list[str]:
    problems: list[str] = []
    lowered = answer.lower()

    if case["expect_not_found"]:
        if not not_found:
            problems.append("expected refusal (not_found), got an answer")
    else:
        if not_found:
            problems.append("unexpected refusal")
        if case["must_contain_any"] and not any(
            needle.lower() in lowered for needle in case["must_contain_any"]
        ):
            problems.append(f"missing all of {case['must_contain_any']}")
        if not citation_docs:
            problems.append("no citations on an affirmative answer")
        elif case["expect_citation_docs"] and not (
            set(citation_docs) & set(case["expect_citation_docs"])
        ):
            problems.append(f"citations {citation_docs} miss {case['expect_citation_docs']}")

    problems.extend(
        f"forbidden text present: {needle!r}"
        for needle in case["must_not_contain"]
        if needle.lower() in lowered
    )
    return problems


async def evaluate_live(*, case_timeout: float = 120) -> object:
    from _results import evaluate_cases
    from faithfulness_judge import JudgeContractError, judge_answer

    async def execute(case):
        evidence = []
        async with get_sessionmaker()() as session:
            resp = await answer_question(
                session,
                case["question"],
                session_id=f"eval:rag:{case['id']}",
                evidence_sink=evidence.extend,
            )
        problems = score_case(
            case, resp.answer, [c.doc_path for c in resp.citations], resp.not_found
        )
        try:
            judged, details = await judge_answer(
                case["question"],
                resp.answer,
                evidence,
                [c.model_dump() for c in resp.citations],
                not_found=resp.not_found,
                case_id=case["id"],
            )
        except JudgeContractError as exc:
            exc.eval_detail.update(
                answer=resp.answer,
                evidence=evidence,
                citations=[c.model_dump() for c in resp.citations],
            )
            raise
        return {
            "problems": problems + judged,
            "model": resp.model,
            "evidence": evidence,
            "answer": resp.answer,
            "not_found": resp.not_found,
            "citations": [c.model_dump() for c in resp.citations],
            **details,
        }

    return await evaluate_cases("rag", load_cases(), execute, case_timeout=case_timeout)


async def run() -> int:
    from _results import require_eval_database

    require_eval_database()
    result = await evaluate_live()
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()
    sys.exit(asyncio.run(run()))
