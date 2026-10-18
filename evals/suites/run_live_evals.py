"""Combined live suites. Ordering gates remain mandatory; new quality gates need calibration.

One ordering invocation per case, reused by tone. Reports are written even when
required execution fails; required errors are never treated as passing quality.
"""

import argparse
import asyncio
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import fetch_menu_snapshot, load_cases, pin_eval_clock, run_case  # noqa: E402
from _results import (  # noqa: E402
    SuiteResult,
    evaluate_cases,
    load_config,
    provenance,
    require_eval_database,
)
from guardrail_bypass_eval import SAFETY_TAGS, find_bypasses  # noqa: E402
from order_agent_eval import score_case  # noqa: E402
from tone_judge_eval import judge_reply, select_tone_cases  # noqa: E402
from tool_correctness_eval import check_invariants  # noqa: E402

from dosadash_ai.db import get_sessionmaker  # noqa: E402
from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_telemetry  # noqa: E402

ORDER_ACCURACY_GATE = float(os.environ.get("ORDER_ACCURACY_GATE", "0.95"))
TONE_GATE = float(os.environ.get("TONE_GATE", "0.8"))
LANGUAGE_GATES = {"ta": float(os.environ.get("TA_ACCURACY_GATE", "0.8"))}
MODULES = {
    "retrieval": "retrieval_eval",
    "rag": "rag_answer_eval",
    "support": "support_agent_eval",
    "inventory": "inventory_agent_eval",
    "copilot": "copilot_eval",
}


async def order_suite(case_timeout: float) -> tuple[SuiteResult, dict, dict]:
    cases = load_cases()
    responses = {}
    async with get_sessionmaker()() as session:
        menu = await fetch_menu_snapshot(session)

    async def execute(case):
        async with get_sessionmaker()() as session:
            result = await run_case(session, case)
        responses[case["id"]] = result.response
        problems = score_case(case, result.response)
        violations = check_invariants(case, result.response, menu)
        safety = bool(SAFETY_TAGS & set(case.get("tags", [])))
        bypasses = find_bypasses(case, result.response) if safety else []
        return {
            "problems": problems + violations,
            "accuracy_problems": problems,
            "tool_violations": violations,
            "bypasses": bypasses,
            "safety": safety,
            "model": result.response.model,
            "reply": result.response.reply,
        }

    result = await evaluate_cases("order", cases, execute, case_timeout=case_timeout)
    count = len(cases)
    metrics = {
        "order_accuracy": sum(
            c["status"] != "error" and not c.get("accuracy_problems") for c in result.cases
        )
        / max(count, 1),
        "tool_correctness": sum(
            c["status"] != "error" and not c.get("tool_violations") for c in result.cases
        )
        / max(count, 1),
        "guardrail_bypasses": sum(len(c.get("bypasses", [])) for c in result.cases),
        "guardrail_cases": sum(bool(SAFETY_TAGS & set(c.get("tags", []))) for c in cases),
    }
    for lang in sorted({c["language"] for c in cases}):
        selected = [c for c in result.cases if c["language"] == lang]
        metrics[f"lang_accuracy_{lang}"] = sum(
            c["status"] != "error" and not c.get("accuracy_problems") for c in selected
        ) / len(selected)
    # Never weaken established gates via environment overrides.
    failed = (
        metrics["order_accuracy"] < max(0.95, ORDER_ACCURACY_GATE)
        or metrics["tool_correctness"] < 1
        or metrics["guardrail_bypasses"] > 0
        or any(
            metrics.get(f"lang_accuracy_{lang}", 0) < max(0.8, gate)
            for lang, gate in LANGUAGE_GATES.items()
        )
    )
    if result.status != "error":
        result.status = "fail" if failed else "pass"
    result.metrics.update(metrics)
    return result, responses, metrics


async def tone_suite(responses: dict, case_timeout: float) -> SuiteResult:
    async def execute(case):
        if case["id"] not in responses:
            raise RuntimeError("ordering response missing; tone cannot be measured")
        verdict = await judge_reply(case, responses[case["id"]].reply)
        return {
            "problems": ["tone score below 4/5"] if verdict.score < 4 else [],
            "score": verdict.score,
            "reply": responses[case["id"]].reply,
            "reason": verdict.reason,
        }

    result = await evaluate_cases(
        "tone", select_tone_cases(load_cases()), execute, case_timeout=case_timeout
    )
    if result.status != "error":
        result.metrics["tone"] = sum(c["score"] for c in result.cases) / (5 * len(result.cases))
        result.status = (
            "pass"
            if result.metrics["tone"]
            >= max(TONE_GATE, load_config()["suites"]["tone"]["threshold"])
            else "fail"
        )
    return result


async def run(
    with_tone: bool = True,
    *,
    profile: str = "pr",
    suites: list[str] | None = None,
) -> tuple[int, dict]:
    config = load_config()
    limits = config["profiles"][profile]
    selected = (
        suites if suites is not None else ["retrieval", "rag", "support", "inventory", "copilot"]
    )
    if set(selected) - MODULES.keys():
        raise ValueError("unknown selected suite")
    metadata = provenance(profile)
    payload = {
        "ran_at": datetime.now(UTC).isoformat(),
        "git_sha": metadata["git_sha"],
        "cases": len(load_cases()),
        "metrics": {"order_accuracy": 0.0, "tool_correctness": 0.0},
        "gates_passed": False,
        "failures": [],
        "case_reports": [],
        "suite_results": [],
        "run_metadata": metadata,
    }
    telemetry = EvalTelemetry(
        max_calls=limits["max_provider_calls"],
        max_tokens=limits["max_tokens"],
        max_cost_usd=limits["max_cost_usd"],
    )
    token = active_telemetry.set(telemetry)
    results = []
    responses = {}
    try:
        require_eval_database()
        pin_eval_clock()
        from sqlalchemy import text

        async with get_sessionmaker()() as session:
            fixture = (
                (
                    await session.execute(
                        text(
                            "SELECT (SELECT count(*) FROM menu_items) AS menu_items, "
                            "(SELECT count(*) FROM orders) AS orders, "
                            "(SELECT max(placed_at)::text FROM orders) AS latest_order"
                        )
                    )
                )
                .mappings()
                .one()
            )
            payload["run_metadata"]["database_snapshot"] = dict(fixture)
        names = ["order", *selected, *(["tone"] if with_tone else [])]
        for name in names:
            try:
                if name == "order":
                    result, responses, metrics = await asyncio.wait_for(
                        order_suite(limits["case_timeout_seconds"]), limits["suite_timeout_seconds"]
                    )
                    payload["metrics"].update(metrics)
                else:
                    coro = (
                        tone_suite(responses, limits["case_timeout_seconds"])
                        if name == "tone"
                        else (
                            importlib.import_module(MODULES[name]).evaluate_live(
                                case_timeout=limits["case_timeout_seconds"]
                            )
                        )
                    )
                    result = await asyncio.wait_for(coro, limits["suite_timeout_seconds"])
                if name == "tone" and "tone" in result.metrics:
                    payload["metrics"]["tone"] = result.metrics["tone"]
            except Exception as exc:  # noqa: BLE001 — report failed suites, run independent ones
                spec = config["suites"][name]
                result = SuiteResult(
                    name=name,
                    status="error",
                    blocking=spec["blocking"],
                    calibrated=spec["calibrated"],
                    reason=type(exc).__name__,
                )
            results.append(result)
            print(f"{name}: {result.status} ({len(result.cases)} cases)")
    except Exception as exc:  # noqa: BLE001 — preflight must also emit a failed report
        payload["failures"].append(f"preflight: {type(exc).__name__}")
        print(f"preflight failed: {type(exc).__name__}; check local evaluation configuration")
    finally:
        active_telemetry.reset(token)
    payload["suite_results"] = [r.model_dump() for r in results]
    payload["run_metadata"]["usage"] = telemetry.summary()
    for result in results:
        if result.gate_failed:
            payload["failures"].append(f"{result.name}: {result.status}")
        for case in result.cases:
            payload["case_reports"].append(
                {
                    "id": case["id"],
                    "language": case["language"],
                    "tags": case["tags"],
                    "suite": result.name,
                    "status": case["status"],
                    "accuracy_problems": case.get("accuracy_problems", case.get("problems", [])),
                    "tool_violations": case.get("tool_violations", []),
                    "bypasses": case.get("bypasses", []),
                    "error": case.get("error"),
                }
            )
    payload["gates_passed"] = bool(results) and not payload["failures"]
    return int(not payload["gates_passed"]), payload


def main() -> int:
    from dosadash_ai.llm import configure_tracing

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-tone", action="store_true", help="compatibility: tone is enabled by default"
    )
    parser.add_argument("--profile", choices=["pr", "full"], default="pr")
    parser.add_argument(
        "--suites", default=",".join(MODULES), help="comma-separated additional suites"
    )
    parser.add_argument("--json", default="live_eval_results.json")
    args = parser.parse_args()
    configure_tracing()
    try:
        exit_code, payload = asyncio.run(
            run(profile=args.profile, suites=[s for s in args.suites.split(",") if s])
        )
    except Exception as exc:  # noqa: BLE001 — malformed config/assets still need a failure artifact
        exit_code = 1
        payload = {
            "ran_at": datetime.now(UTC).isoformat(),
            "cases": 0,
            "metrics": {"order_accuracy": 0.0, "tool_correctness": 0.0},
            "gates_passed": False,
            "failures": [f"startup: {type(exc).__name__}"],
            "case_reports": [],
            "suite_results": [],
            "run_metadata": {"schema_version": 1, "profile": args.profile},
        }
    Path(args.json).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"results written to {args.json}; gates_passed={payload['gates_passed']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
