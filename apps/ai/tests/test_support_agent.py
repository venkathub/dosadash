"""Support agent guardrail + fallback tests (no provider keys needed)."""

from dosadash_ai.llm.client import LLMError
from dosadash_ai.support import agent as support_agent
from dosadash_ai.support.agent import apply_guardrail, build_messages
from dosadash_shared import SupportAgentRequest, SupportTurn

CONTEXT = [
    {
        "order_id": 41,
        "status": "COOKING",
        "total": "315.00",
        "placed_at": "2026-08-18T12:00:00",
        "items": ["2× Masala Dosa"],
    },
    {
        "order_id": 37,
        "status": "DELIVERED",
        "total": "220.00",
        "placed_at": "2026-08-17T20:00:00",
        "items": ["1× Chicken Biryani"],
    },
]


def test_known_order_actions_pass_through():
    turn, violations = apply_guardrail(
        SupportTurn(reply="On it.", action="get_status", order_id=41), CONTEXT
    )
    assert turn.action == "get_status" and turn.order_id == 41
    assert violations == []


def test_unknown_order_id_downgraded():
    turn, violations = apply_guardrail(
        SupportTurn(reply="Refunding order 999.", action="refund_request", order_id=999), CONTEXT
    )
    assert turn.action == "answer"
    assert turn.order_id is None
    assert "double-check" in turn.reply
    assert violations


def test_action_without_order_id_downgraded():
    turn, violations = apply_guardrail(
        SupportTurn(reply="Cancelled!", action="cancel_order", order_id=None), CONTEXT
    )
    assert turn.action == "answer"
    assert violations


def test_escalate_with_foreign_order_id_keeps_ticket_drops_id():
    turn, violations = apply_guardrail(
        SupportTurn(reply="Escalating.", action="escalate", order_id=12345, reason="upset"),
        CONTEXT,
    )
    assert turn.action == "escalate"
    assert turn.order_id is None
    assert violations


def test_messages_redact_phones_and_carry_context():
    request = SupportAgentRequest(
        user_id=7, message="call me on +91 98765 43210 about order 41", history=[]
    )
    messages = build_messages(request, CONTEXT)
    assert "98765" not in messages[-1]["content"]  # Hard Rule 8
    assert '"order_id": 41' in messages[1]["content"]


async def test_llm_failure_becomes_escalation(monkeypatch):
    async def boom(**_):
        raise LLMError("all models failed")

    async def fake_context(session, user_id):
        return CONTEXT

    monkeypatch.setattr(support_agent, "structured_completion", boom)
    monkeypatch.setattr(support_agent, "load_order_context", fake_context)

    response = await support_agent.support_turn(
        None, SupportAgentRequest(user_id=7, message="help")
    )
    assert response.turn.action == "escalate"
    assert response.model is None
    assert response.violations
