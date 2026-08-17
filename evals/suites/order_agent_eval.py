"""Live order_accuracy eval — the flagship suite (Phase 4 merge gate ≥ 0.95).

Runs the full LangGraph agent (real LLM chain + real DB) over golden
conversations, scoring draft correctness, guardrail behavior, refusals,
and readiness gating. Requires keys and a seeded database:

    uv run python -m dosadash_api.seed          # menu must exist
    uv run python evals/suites/order_agent_eval.py

Mutates settings/menu rows for paused/86'd cases and ALWAYS restores them.
NOT run in CI yet (key-free gates: test_order_agent_assets.py).

    PASS_THRESHOLD=0.95 uv run python evals/suites/order_agent_eval.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.graph import run_turn
from dosadash_ai.db import get_sessionmaker
from dosadash_shared import AgentChatRequest, AgentMessage, OrderDraft, OrderDraftItem

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "order_conversations.jsonl"
PASS_THRESHOLD = float(os.environ.get("PASS_THRESHOLD", "0.8"))
EVAL_USER_PHONE = "+919999900042"  # dedicated eval user, upserted per run


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


async def _resolve_draft(session: AsyncSession, lines: list[dict]) -> OrderDraft:
    items = []
    for line in lines:
        row = (
            await session.execute(
                text("SELECT id, name, price FROM menu_items WHERE name = :n"), {"n": line["name"]}
            )
        ).one()
        items.append(
            OrderDraftItem(
                item_id=row.id, name=row.name, qty=line["qty"] or 1, unit_price=row.price
            )
        )
    return OrderDraft(items=items, subtotal=sum((i.unit_price * i.qty for i in items), 0))


async def _ensure_eval_user(session: AsyncSession, prefs: dict) -> int:
    user_id = await session.scalar(
        text(
            "INSERT INTO users (phone, name, role) VALUES (:p, 'Eval User', 'CUSTOMER') "
            "ON CONFLICT (phone) DO UPDATE SET name = 'Eval User' RETURNING id"
        ),
        {"p": EVAL_USER_PHONE},
    )
    await session.execute(
        text(
            "INSERT INTO user_preferences (user_id, diet, allergens, spice_level, language) "
            "VALUES (:u, :d, :a, 2, :lang) ON CONFLICT (user_id) DO UPDATE SET "
            "diet = :d, allergens = :a, language = :lang"
        ),
        {
            "u": user_id,
            "d": prefs.get("diet"),
            "a": prefs.get("allergens", []),
            "lang": prefs.get("language", "en"),
        },
    )
    await session.commit()
    return user_id


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


async def _apply_setup(session: AsyncSession, case: dict) -> None:
    if case["kitchen"] == "paused":
        await session.execute(text("UPDATE settings SET kitchen_paused = true WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = false WHERE name = :n"), {"n": name}
        )
    await session.commit()


async def _restore(session: AsyncSession, case: dict) -> None:
    await session.execute(text("UPDATE settings SET kitchen_paused = false WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = true WHERE name = :n"), {"n": name}
        )
    await session.commit()


async def run() -> int:
    cases = load_cases()
    passed = 0
    async with get_sessionmaker()() as session:
        for case in cases:
            user_id = await _ensure_eval_user(session, case["user"]) if case.get("user") else None
            try:
                await _apply_setup(session, case)
                request = AgentChatRequest(
                    message=case["message"],
                    history=[AgentMessage(**m) for m in case["history"]],
                    draft=await _resolve_draft(session, case["draft"]) if case["draft"] else None,
                    user_id=user_id,
                    session_id=f"eval:{case['id']}",
                )
                resp = await run_turn(session, request)
                problems = score_case(case, resp)
            finally:
                await _restore(session, case)
            status = "PASS" if not problems else "FAIL"
            print(f"[{status}] {case['id']} ({case['language']}): {problems or 'ok'}")
            if problems:
                print(f"         reply: {resp.reply[:110]!r}")
                print(f"         draft: {[(i.name, i.qty) for i in resp.draft.items]}")
            passed += not problems

    rate = passed / len(cases)
    print(f"\norder_agent eval (order_accuracy): {passed}/{len(cases)} passed ({rate:.0%})")
    print(f"threshold: {PASS_THRESHOLD:.0%} (Phase 4 merge gate: 0.95)")
    return 0 if rate >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    from dosadash_ai.llm import configure_tracing

    configure_tracing()  # trace live eval runs in Langfuse (Hard Rule 6)
    sys.exit(asyncio.run(run()))
