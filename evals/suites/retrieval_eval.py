"""Live retrieval eval — hit@k of hybrid search over the golden queries.

Requires real embedding keys AND an ingested database:

    uv run python -m dosadash_ai.rag.ingest --knowledge-dir knowledge
    uv run python evals/suites/retrieval_eval.py

NOT run in CI (CI runs the key-free asset gates in
test_retrieval_assets.py); joins the merge gates in Phase 4.

    PASS_THRESHOLD=0.85 TOP_K=4 uv run python evals/suites/retrieval_eval.py
"""

import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.client import embed_texts
from dosadash_ai.rag.search import hybrid_search

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "retrieval.jsonl"
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))
TOP_K = int(os.environ.get("TOP_K", "4"))


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


async def run() -> int:
    cases = load_cases()
    embeddings = await embed_texts([c["query"] for c in cases], trace_name="eval.retrieval.embed")
    passed = 0
    by_language: dict[str, list[bool]] = defaultdict(list)

    async with get_sessionmaker()() as session:
        for case, embedding in zip(cases, embeddings, strict=True):
            results = await hybrid_search(session, case["query"], embedding, top_k=TOP_K)
            got_docs = [r.chunk.doc_path for r in results]
            hit = any(doc in case["expect_docs"] for doc in got_docs)
            by_language[case["language"]].append(hit)
            passed += hit
            status = "PASS" if hit else "FAIL"
            print(f"[{status}] {case['id']} ({case['language']}): {case['query'][:60]!r}")
            if not hit:
                print(f"         wanted {case['expect_docs']}, got {got_docs}")

    rate = passed / len(cases)
    print(f"\nretrieval eval: {passed}/{len(cases)} hit@{TOP_K} ({rate:.0%})")
    for language, hits in sorted(by_language.items()):
        print(f"  {language}: {sum(hits)}/{len(hits)}")
    print(f"threshold: {PASS_THRESHOLD:.0%}")
    return 0 if rate >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # trace live eval runs in Langfuse (Hard Rule 6)
    sys.exit(asyncio.run(run()))
