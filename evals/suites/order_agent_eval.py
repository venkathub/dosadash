"""Live order_accuracy eval — the flagship suite (Phase 4 merge gate ≥ 0.95).

Runs the full LangGraph agent (real LLM chain + real DB) over golden
conversations, scoring draft correctness, guardrail behavior, refusals,
and readiness gating. Requires keys and a seeded database:

    uv run python -m dosadash_api.seed          # menu must exist
    PASS_THRESHOLD=0.95 uv run python evals/suites/order_agent_eval.py

Execution lives in _harness.py (shared, one pass per case); for the
combined multi-metric run used by the CI gate see run_live_evals.py.
Key-free CI asset gates: test_order_agent_assets.py.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import CaseResult, load_cases, run_all  # noqa: E402

from dosadash_ai.db import get_sessionmaker  # noqa: E402

PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))


def score_case(case: dict, resp) -> list[str]:
    expect = case["expect"]
    problems: list[str] = []
    got = {i.name: i.qty for i in resp.draft.items}
    reply = resp.reply.lower()

    if expect.get("draft") is not None:
        want = {line["name"]: line["qty"] for line in expect["draft"]}
        for name, qty in want.items():
            if name not in got:
                problems.append(f"draft missing {name}")
            elif qty is not None and got[name] != qty:
                problems.append(f"{name} qty {got[name]} != {qty}")
        problems.extend(f"unexpected draft item {name}" for name in got if name not in want)

    for forbidden in expect.get("forbid_names", []):
        if any(forbidden.lower() in name.lower() for name in got):
            problems.append(f"forbidden item drafted: {forbidden}")

    if expect.get("ready") is not None and resp.ready_to_place != expect["ready"]:
        problems.append(f"ready_to_place {resp.ready_to_place} != {expect['ready']}")

    if expect.get("reply_contains_any") and not any(
        needle.lower() in reply for needle in expect["reply_contains_any"]
    ):
        problems.append(f"reply missing all of {expect['reply_contains_any']}")
    problems.extend(
        f"reply contains forbidden {needle!r}"
        for needle in expect.get("reply_forbids", [])
        if needle.lower() in reply
    )
    if expect.get("warnings_contain_any") and not any(
        needle.lower() in w.lower()
        for w in resp.warnings
        for needle in expect["warnings_contain_any"]
    ):
        problems.append(f"warnings missing all of {expect['warnings_contain_any']}")
    return problems


def report(results: list[CaseResult]) -> float:
    passed = 0
    for result in results:
        problems = score_case(result.case, result.response)
        status = "PASS" if not problems else "FAIL"
        print(f"[{status}] {result.case['id']} ({result.case['language']}): {problems or 'ok'}")
        if problems:
            print(f"         reply: {result.response.reply[:110]!r}")
            print(f"         draft: {[(i.name, i.qty) for i in result.response.draft.items]}")
        passed += not problems
    rate = passed / len(results)
    print(f"\norder_agent eval (order_accuracy): {passed}/{len(results)} passed ({rate:.0%})")
    print(f"threshold: {PASS_THRESHOLD:.0%} (Phase 4 merge gate: 0.95)")
    return rate


async def run() -> int:
    async with get_sessionmaker()() as session:
        results = await run_all(session, load_cases())
    return 0 if report(results) >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # trace live eval runs in Langfuse (Hard Rule 6)
    sys.exit(asyncio.run(run()))
