"""Live tool_correctness eval (Phase 4).

Scores the *mechanism*, not the conversation: whatever the model said,
every AgentChatResponse must satisfy DB-anchored invariants — real
item_ids, DB names/prices (never the model's), sane quantities, correct
subtotal arithmetic, honest kitchen/ready gating, and no 86'd items.
These are guaranteed by the Hard Rule 2 guardrail *when it is wired in*;
this suite exists to catch anyone routing agent output around it.

    uv run python evals/suites/tool_correctness_eval.py

Expected score is 1.0 — any violation is a wiring regression.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import CaseResult, MenuRow, fetch_menu_snapshot, load_cases, run_all  # noqa: E402

from dosadash_ai.db import get_sessionmaker  # noqa: E402

PASS_THRESHOLD = 1.0  # guardrail invariants are all-or-nothing


def check_invariants(case: dict, resp, menu: dict[int, MenuRow]) -> list[str]:
    """DB-anchored response invariants → list of violations (empty = ok)."""
    violations: list[str] = []
    seen_ids: set[int] = set()

    for item in resp.draft.items:
        row = menu.get(item.item_id)
        if row is None:
            violations.append(f"item_id {item.item_id} not in menu_items (hallucinated id)")
            continue
        if item.name != row.name:
            violations.append(f"name {item.name!r} != DB {row.name!r} (model-supplied name)")
        if Decimal(item.unit_price) != row.price:
            violations.append(f"{row.name}: price {item.unit_price} != DB {row.price}")
        if item.item_id in seen_ids:
            violations.append(f"duplicate item_id {item.item_id} (merge failed)")
        seen_ids.add(item.item_id)
        if not 1 <= item.qty <= 20:
            violations.append(f"{row.name}: qty {item.qty} outside 1..20")

    expected_subtotal = sum((Decimal(i.unit_price) * i.qty for i in resp.draft.items), Decimal("0"))
    if Decimal(resp.draft.subtotal) != expected_subtotal:
        violations.append(f"subtotal {resp.draft.subtotal} != {expected_subtotal}")

    drafted_names = {i.name for i in resp.draft.items}
    for name in case.get("setup", {}).get("make_unavailable", []):
        if name in drafted_names:
            violations.append(f"86'd item {name!r} entered the draft")

    if case["kitchen"] == "paused":
        if resp.kitchen_open:
            violations.append("kitchen_open true while paused")
        if resp.ready_to_place:
            violations.append("ready_to_place true while paused")
    if resp.ready_to_place and not resp.draft.items:
        violations.append("ready_to_place true with empty draft")
    if resp.ready_to_place and not resp.kitchen_open:
        violations.append("ready_to_place true with closed kitchen")
    return violations


def report(results: list[CaseResult], menu: dict[int, MenuRow]) -> float:
    passed = 0
    for result in results:
        violations = check_invariants(result.case, result.response, menu)
        status = "PASS" if not violations else "FAIL"
        print(f"[{status}] {result.case['id']}: {violations or 'ok'}")
        passed += not violations
    rate = passed / len(results)
    print(f"\ntool_correctness: {passed}/{len(results)} ({rate:.0%}) — required: 100%")
    return rate


async def run() -> int:
    async with get_sessionmaker()() as session:
        menu = await fetch_menu_snapshot(session)
        results = await run_all(session, load_cases())
    return 0 if report(results, menu) >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # Hard Rule 6
    sys.exit(asyncio.run(run()))
