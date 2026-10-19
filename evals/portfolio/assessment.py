"""One bounded live assessment, including knowledge ingestion in the same ledger.

Requires an isolated *_eval database already migrated and seeded. Never creates
orders. Provider permission is checked before ingestion. Output is never replaced.
"""

import argparse
import asyncio
import json
from pathlib import Path

from evals.suites._results import require_eval_database

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm import configure_tracing
from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_case, active_telemetry
from dosadash_ai.rag.ingest import ingest_knowledge_dir

from .runner import asset, read_cases, run


async def assess(manifest: Path) -> dict:
    require_eval_database()
    cases = read_cases(manifest)
    if not all(c.provider_use_allowed for c in cases):
        raise ValueError("provider permission missing")
    if sum(c.task == "audio" for c in cases) > 6:
        raise ValueError("at most six audio clips")
    for case in cases:
        if case.path:
            asset(case, manifest)
    configure_tracing()
    ledger = EvalTelemetry(max_calls=300, max_tokens=200_000, max_cost_usd=1)
    token = active_telemetry.set(ledger)
    case_token = active_case.set("knowledge-ingestion")
    try:
        async with get_sessionmaker()() as session:
            ingestion = await ingest_knowledge_dir(session, Path("knowledge"))
        result = await run(manifest, "live", ledger=ledger)
        result["knowledge_ingestion"] = vars(ingestion)
        return result
    except Exception as exc:
        return {"status": "error", "error": type(exc).__name__, "usage": ledger.summary()}
    finally:
        active_case.reset(case_token)
        active_telemetry.reset(token)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as output:
        result = asyncio.run(assess(args.manifest))
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    return int(result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
