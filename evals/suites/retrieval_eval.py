"""Live retrieval eval — hit@k of hybrid search over the golden queries.

Requires real embedding keys AND an ingested database:

    uv run python -m dosadash_ai.rag.ingest --knowledge-dir knowledge
    uv run python evals/suites/retrieval_eval.py

Run by the combined CI runner; see evals/config.json for calibration and gate policy.

    TOP_K=4 uv run python evals/suites/retrieval_eval.py
"""

import asyncio
import os
import sys

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.client import embed_texts
from dosadash_ai.rag.search import hybrid_search

TOP_K = int(os.environ.get("TOP_K", "4"))


def load_cases() -> list[dict]:
    from _results import load_jsonl

    return load_jsonl("retrieval.jsonl")


async def evaluate_live(*, case_timeout: float = 120) -> object:
    from _results import evaluate_cases

    async def execute(case):
        [embedding] = await embed_texts([case["query"]], trace_name="eval.retrieval.embed")
        async with get_sessionmaker()() as session:
            results = await hybrid_search(session, case["query"], embedding, top_k=TOP_K)
        docs = [r.chunk.doc_path for r in results]
        ranks = [i for i, doc in enumerate(docs, 1) if doc in case["expect_docs"]]
        return {
            "problems": [] if ranks else ["no relevant document retrieved"],
            "retrieved_docs": docs,
            "reciprocal_rank": 1 / min(ranks) if ranks else 0,
        }

    result = await evaluate_cases("retrieval", load_cases(), execute, case_timeout=case_timeout)
    result.metrics["hit_at_k"] = result.metrics["pass_rate"]
    result.metrics["mrr_at_k"] = sum(c.get("reciprocal_rank", 0) for c in result.cases) / max(
        len(result.cases), 1
    )
    return result


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
