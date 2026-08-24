"""Shared live-eval harness (Phase 4).

One execution pass over the golden order conversations, reused by every
live suite (order_accuracy, tool_correctness, guardrail_bypass, tone) so
a combined run costs exactly one agent invocation per case.

Requires keys and a seeded database:

    uv run python -m dosadash_api.seed          # menu must exist

Mutates settings/menu rows for paused/86'd cases and ALWAYS restores them.
"""

import json
import os
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

# Phase 11: every dish now carries a hard serving window, so the live gate
# pins the availability clock to a canonical instant instead of banning
# scheduled dishes from expectations. 19:30 IST = tiffin + lunch-dinner +
# snack windows are ALL open; morning-only (pongal, Mini Tiffin) and
# lunch-only (Non-Veg Mess Meals) dishes are deterministically OFF — the
# serving_window golden cases rely on exactly that. The asset gate
# (test_no_time_dependent_expectations) verifies every expected dish is
# on-schedule at this instant. Only the availability clock is pinned
# (dosadash_shared.availability.now_ist); nothing else reads the env var.
EVAL_CLOCK_IST = "2026-08-20T19:30:00"


def pin_eval_clock() -> None:
    os.environ.setdefault("DOSADASH_NOW_IST", EVAL_CLOCK_IST)


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


async def _seed_usual_orders(session: AsyncSession, user_id: int, spec: dict) -> None:
    """Phase 6 memory cases: give the eval user a repeated order history so
    the context loader derives a "usual". Cleaned up in restore()."""
    brand_id = await session.scalar(text("SELECT MIN(id) FROM brands"))
    for i in range(spec.get("times", 3)):
        order_id = await session.scalar(
            text(
                "INSERT INTO orders (user_id, brand_id, channel, status, subtotal, gst, total, "
                "placed_at) VALUES (:u, :b, 'WEB', 'DELIVERED', 100, 5, 105, "
                "now() - make_interval(days => :age)) RETURNING id"
            ),
            {"u": user_id, "b": brand_id, "age": i * 7 + 1},
        )
        for line in spec["items"]:
            await session.execute(
                text(
                    "INSERT INTO order_items (order_id, item_id, qty, unit_price) "
                    "SELECT :o, id, :q, price FROM menu_items WHERE name = :n"
                ),
                {"o": order_id, "q": line["qty"], "n": line["name"]},
            )


async def apply_setup(session: AsyncSession, case: dict, user_id: int | None = None) -> None:
    if case["kitchen"] == "paused":
        await session.execute(text("UPDATE settings SET kitchen_paused = true WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = false WHERE name = :n"), {"n": name}
        )
    # Phase 7 l10n: approved translations become agent aliases. Seeded per
    # case (not globally) so every non-Tamil case keeps the byte-identical
    # pre-l10n menu context. Cleaned up in restore().
    for trans in case.get("setup", {}).get("seed_translations", []):
        await session.execute(
            text(
                "INSERT INTO menu_item_translations "
                "(item_id, lang, name, status, model, prompt_version) "
                "SELECT id, :lang, :text, 'APPROVED', 'eval-harness', 'menu_translation_v1' "
                "FROM menu_items WHERE name = :n "
                "ON CONFLICT (item_id, lang) DO UPDATE "
                "SET name = :text, status = 'APPROVED'"
            ),
            {"lang": trans["lang"], "text": trans["text"], "n": trans["name"]},
        )
    seed = case.get("setup", {}).get("seed_usual_orders")
    if seed and user_id is not None:
        await _seed_usual_orders(session, user_id, seed)
    await session.commit()


async def restore(session: AsyncSession, case: dict, user_id: int | None = None) -> None:
    await session.execute(text("UPDATE settings SET kitchen_paused = false WHERE id = 1"))
    for name in case.get("setup", {}).get("make_unavailable", []):
        await session.execute(
            text("UPDATE menu_items SET is_available = true WHERE name = :n"), {"n": name}
        )
    for trans in case.get("setup", {}).get("seed_translations", []):
        await session.execute(
            text(
                "DELETE FROM menu_item_translations WHERE lang = :lang AND item_id = "
                "(SELECT id FROM menu_items WHERE name = :n)"
            ),
            {"lang": trans["lang"], "n": trans["name"]},
        )
    if case.get("setup", {}).get("seed_usual_orders") and user_id is not None:
        await session.execute(
            text(
                "DELETE FROM order_items WHERE order_id IN "
                "(SELECT id FROM orders WHERE user_id = :u)"
            ),
            {"u": user_id},
        )
        await session.execute(text("DELETE FROM orders WHERE user_id = :u"), {"u": user_id})
        await session.execute(text("DELETE FROM user_memories WHERE user_id = :u"), {"u": user_id})
    await session.commit()


async def run_case(session: AsyncSession, case: dict) -> CaseResult:
    """Execute one golden case against the live agent (setup + restore)."""
    user_id = await ensure_eval_user(session, case["user"]) if case.get("user") else None
    try:
        await apply_setup(session, case, user_id)
        request = AgentChatRequest(
            message=case["message"],
            history=[AgentMessage(**m) for m in case["history"]],
            draft=await resolve_draft(session, case["draft"]) if case["draft"] else None,
            user_id=user_id,
            session_id=f"eval:{case['id']}",
        )
        response = await run_turn(session, request)
    finally:
        await restore(session, case, user_id)
    return CaseResult(case=case, response=response)


async def run_all(session: AsyncSession, cases: list[dict]) -> list[CaseResult]:
    return [await run_case(session, case) for case in cases]
