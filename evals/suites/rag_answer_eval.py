"""Live RAG answer eval — grounding, citations, refusals, redaction.

Requires real LLM keys AND an ingested database:

    uv run python -m dosadash_ai.rag.ingest --knowledge-dir knowledge
    uv run python evals/suites/rag_answer_eval.py

NOT run in CI (CI runs the key-free gates in test_rag_answer_assets.py);
joins the merge gates in Phase 4 with an LLM-as-judge faithfulness rubric.

    PASS_THRESHOLD=0.85 uv run python evals/suites/rag_answer_eval.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.rag.answers import answer_question

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "rag_answers.jsonl"
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


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


async def run() -> int:
    cases = load_cases()
    passed = 0
    async with get_sessionmaker()() as session:
        for case in cases:
            resp = await answer_question(session, case["question"], session_id=f"eval:{case['id']}")
            problems = score_case(
                case, resp.answer, [c.doc_path for c in resp.citations], resp.not_found
            )
            status = "PASS" if not problems else "FAIL"
            print(f"[{status}] {case['id']} ({case['language']}): {problems or 'ok'}")
            if problems:
                print(f"         answer: {resp.answer[:120]!r}")
            passed += not problems

    rate = passed / len(cases)
    print(f"\nrag_answer eval: {passed}/{len(cases)} passed ({rate:.0%})")
    print(f"threshold: {PASS_THRESHOLD:.0%}")
    return 0 if rate >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # trace live eval runs in Langfuse (Hard Rule 6)
    sys.exit(asyncio.run(run()))
