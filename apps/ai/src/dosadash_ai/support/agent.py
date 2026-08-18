"""Support agent (Phase 6): status/cancel/refund help with policy guardrails.

Trust boundaries (mirrors the order agent):
- context (the customer's orders) is loaded fresh from the DB — the model
  never learns about orders it wasn't shown;
- the guardrail re-checks every `order_id` the model emits against that
  context (Hard Rule 2 analog: no hallucinated orders) and downgrades
  actions the policy forbids;
- EXECUTION happens in apps/api (state machine, escalation rows). This
  module only decides *what to ask for*.
"""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_ai.redaction import redact_phones
from dosadash_shared import (
    SUPPORT_PROMPT_VERSION,
    SupportAgentRequest,
    SupportAgentResponse,
    SupportTurn,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_ORDERS = 5

_ORDERS_SQL = text(
    """
    SELECT o.id, o.status, o.total, o.placed_at,
           ARRAY_AGG(oi.qty || '× ' || m.name) AS items
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.id
    JOIN menu_items m ON m.id = oi.item_id
    WHERE o.user_id = :user_id
    GROUP BY o.id
    ORDER BY o.placed_at DESC
    LIMIT :limit
    """
)


async def load_order_context(session: AsyncSession, user_id: int) -> list[dict]:
    rows = (
        await session.execute(_ORDERS_SQL, {"user_id": user_id, "limit": MAX_CONTEXT_ORDERS})
    ).fetchall()
    return [
        {
            "order_id": r.id,
            "status": r.status,
            "total": str(r.total),
            "placed_at": r.placed_at.isoformat(),
            "items": list(r.items),
        }
        for r in rows
    ]


def apply_guardrail(turn: SupportTurn, context: list[dict]) -> tuple[SupportTurn, list[str]]:
    """Downgrade anything the policy forbids; never trust the model's ids."""
    violations: list[str] = []
    known_ids = {o["order_id"] for o in context}

    if turn.action in ("get_status", "cancel_order", "refund_request"):
        if turn.order_id is None:
            violations.append(f"{turn.action} without order_id → answer")
            turn = turn.model_copy(update={"action": "answer", "order_id": None})
        elif turn.order_id not in known_ids:
            violations.append(f"unknown order_id {turn.order_id} → answer")
            turn = turn.model_copy(
                update={
                    "action": "answer",
                    "order_id": None,
                    "reply": (
                        "I can't find that order on your account — could you "
                        "double-check the order number?"
                    ),
                }
            )
    if turn.action == "escalate" and turn.order_id is not None and turn.order_id not in known_ids:
        violations.append(f"escalate with unknown order_id {turn.order_id} → cleared id")
        turn = turn.model_copy(update={"order_id": None})
    return turn, violations


def build_messages(request: SupportAgentRequest, context: list[dict]) -> list[dict[str, str]]:
    payload = json.dumps({"today": datetime.now(UTC).date().isoformat(), "orders": context})
    messages: list[dict[str, str]] = [
        {"role": "system", "content": load_prompt(SUPPORT_PROMPT_VERSION)},
        {"role": "system", "content": f"CUSTOMER CONTEXT:\n{payload}"},
    ]
    for msg in request.history[-10:]:
        messages.append({"role": msg.role, "content": redact_phones(msg.content)})
    messages.append({"role": "user", "content": redact_phones(request.message)})
    return messages


async def support_turn(session: AsyncSession, request: SupportAgentRequest) -> SupportAgentResponse:
    context = await load_order_context(session, request.user_id)
    try:
        turn, model = await structured_completion(
            messages=build_messages(request, context),
            response_model=SupportTurn,
            trace_name="support_agent",
            prompt_version=SUPPORT_PROMPT_VERSION,
            session_id=request.session_id,
            user_id=str(request.user_id),
        )
    except LLMError as exc:
        logger.warning("support agent LLM failure: %s", exc)
        return SupportAgentResponse(
            turn=SupportTurn(
                reply=(
                    "Sorry, I'm having trouble right now — I've flagged your message "
                    "for the team to follow up."
                ),
                action="escalate",
                reason="support agent unavailable",
            ),
            violations=["llm unavailable → escalate"],
        )

    turn, violations = apply_guardrail(turn, context)
    return SupportAgentResponse(turn=turn, model=model, violations=violations)
