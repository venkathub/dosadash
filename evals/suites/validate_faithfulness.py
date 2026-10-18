"""Replay retained RAG answers and refusal regressions without regenerating answers.

Run with provider environment configured:
  python evals/suites/validate_faithfulness.py --source-report report.json --json validation.json
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from _results import evaluate_cases, load_jsonl, provenance
from calibrate_judges import run as calibrate
from faithfulness_judge import judge_answer

from dosadash_ai.llm.client import configure_tracing
from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_telemetry


async def run(source: Path, repeats: int) -> dict:
    before = provenance("pr")
    source_bytes = await asyncio.to_thread(source.read_bytes)
    original = json.loads(source_bytes)
    cases = next(s["cases"] for s in original["suite_results"] if s["name"] == "rag")
    questions = {c["id"]: c["question"] for c in load_jsonl("rag_answers.jsonl")}
    replay = [
        {
            **case,
            "id": case["id"].removeprefix("rag:"),
            "question": questions[case["id"].removeprefix("rag:")],
        }
        for case in cases
    ]
    # Require actual historical refusal flags. Expected fixture labels are not substitutes.
    if any("not_found" not in c for c in replay):
        raise ValueError("source report must retain actual not_found flags")
    regressions = [
        {**case, "id": f"{case['id']}:repeat-{repeat}"}
        for repeat in range(1, repeats + 1)
        for case in load_jsonl("faithfulness_regressions.jsonl")
    ]

    async def execute(case):
        problems, details = await judge_answer(
            case["question"],
            case["answer"],
            case["evidence"],
            case["citations"],
            not_found=case["not_found"],
            case_id=case["id"],
        )
        return {
            "problems": problems,
            "answer": case["answer"],
            "not_found": case["not_found"],
            "evidence": case["evidence"],
            "citations": case["citations"],
            **details,
        }

    ledger = EvalTelemetry(max_calls=200, max_cost_usd=0.5)
    token = active_telemetry.set(ledger)
    try:
        calibration = await calibrate()
        regression_result = await evaluate_cases("rag", regressions, execute)
        replay_result = await evaluate_cases("rag", replay, execute)
    finally:
        active_telemetry.reset(token)
    after = provenance("pr")
    unchanged = all(before[k] == after[k] for k in ("source_sha256", "asset_sha256"))
    return {
        "kind": "retained-answer judge validation; not a fresh end-to-end generation run",
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "run_metadata": before,
        "source_and_assets_unchanged": unchanged,
        "calibration": calibration,
        "regressions": regression_result.model_dump(),
        "replay": replay_result.model_dump(),
        "usage": ledger.summary(),
        "passed": unchanged
        and calibration["human_calibration_complete"]
        and all(c["status"] == "pass" for c in regression_result.cases + replay_result.cases),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be between 1 and 10")
    configure_tracing()
    result = asyncio.run(run(args.source_report, args.repeats))
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Saved {args.json}; passed={result['passed']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
