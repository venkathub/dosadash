"""Shared live-eval harness (Phase 4).

One execution pass over the golden order conversations, reused by every
live suite (order_accuracy, tool_correctness, guardrail_bypass, tone) so
a combined run costs exactly one agent invocation per case.

Requires keys and a seeded database:

    uv run python -m dosadash_api.seed          # menu must exist

Mutates settings/menu rows for paused/86'd cases and ALWAYS restores them.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.agent.graph import run_turn
from dosadash_shared import (
    AgentChatRequest,
    AgentChatResponse,
    AgentMessage,
    OrderDraft,
    OrderDraftItem,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "order_conversations.jsonl"
EVAL_USER_PHONE = "+919999900042"  # dedicated eval user, upserted per run


@dataclass(frozen=True)
class MenuRow:
    id: int
    name: str
    price: Decimal


@dataclass
class CaseResult:
    case: dict
    response: AgentChatResponse


def load_cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


async def fetch_menu_snapshot(session: AsyncSession) -> dict[int, MenuRow]:
    """id → (name, price) — the authority tool_correctness checks against."""
    rows = await session.execute(text("SELECT id, name, price FROM menu_items"))
    return {row.id: MenuRow(id=row.id, name=row.name, price=Decimal(row.price)) for row in rows}


async def resolve_draft(session: AsyncSession, lines: list[dict]) -> OrderDraft:
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


async def ensure_eval_user(session: AsyncSession, prefs: dict) -> int:
    user_id = await session.scalar(
        text(
            "INSERT INTO users (phone, name, role, loyalty_points) "
            "VALUES (:p, 'Eval User', 'customer', 0) "
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
            "d": prefs["diet"].upper() if prefs.get("diet") else None,  # Diet enum is UPPERCASE
            "a": prefs.get("allergens", []),
            "lang": prefs.get("language", "en"),
        },
    )
    await session.commit()
    return user_id


async def apply_setup(session: AsyncSession, case: dict) -> None:
    if case["kitchen"] == "paused":
        await session.execute(text("UPDATE settings SET kitchen_paused = true WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = false WHERE name = :n"), {"n": name}
        )
    await session.commit()


async def restore(session: AsyncSession, case: dict) -> None:
    await session.execute(text("UPDATE settings SET kitchen_paused = false WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = true WHERE name = :n"), {"n": name}
        )
    await session.commit()


async def run_case(session: AsyncSession, case: dict) -> CaseResult:
    """Execute one golden case against the live agent (setup + restore)."""
    user_id = await ensure_eval_user(session, case["user"]) if case.get("user") else None
    try:
        await apply_setup(session, case)
        request = AgentChatRequest(
            message=case["message"],
            history=[AgentMessage(**m) for m in case["history"]],
            draft=await resolve_draft(session, case["draft"]) if case["draft"] else None,
            user_id=user_id,
            session_id=f"eval:{case['id']}",
        )
        response = await run_turn(session, request)
    finally:
        await restore(session, case)
    return CaseResult(case=case, response=response)


async def run_all(session: AsyncSession, cases: list[dict]) -> list[CaseResult]:
    return [await run_case(session, case) for case in cases]
