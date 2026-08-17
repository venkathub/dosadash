"""Order-agent graph against real PostgreSQL — LLM mocked, everything else real."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from dosadash_ai.agent import graph as graph_mod
from dosadash_ai.agent.graph import run_turn
from dosadash_shared import (
    AgentChatRequest,
    AgentMessage,
    AgentTurn,
    DraftItemIn,
    OrderDraft,
    OrderDraftItem,
)


@pytest.fixture
def fake_llm(monkeypatch):
    """Set the model's turn per-test; captures the messages it was shown."""
    state = {"turn": AgentTurn(reply="Done!"), "kwargs": None}

    async def fake_completion(**kwargs):
        state["kwargs"] = kwargs
        return state["turn"], "gpt-4o-mini"

    async def fake_embed(texts, **_):
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(graph_mod, "structured_completion", fake_completion)
    monkeypatch.setattr(graph_mod, "embed_texts", fake_embed)
    return state


def _context_payload(state) -> dict:
    messages = state["kwargs"]["messages"]
    assert messages[1]["content"].startswith("MENU: ")  # stable, cacheable prefix
    assert messages[2]["content"].startswith("STATE: ")  # volatile per-turn state
    menu = json.loads(messages[1]["content"].removeprefix("MENU: "))
    volatile = json.loads(messages[2]["content"].removeprefix("STATE: "))
    return {**menu, **volatile}


async def test_turn_validates_items_against_db(agent_session, fake_llm):
    fake_llm["turn"] = AgentTurn(
        reply="Added!",
        draft_items=[
            DraftItemIn(item_id=1, qty=2),  # Masala Dosa — real
            DraftItemIn(item_id=999, qty=1),  # hallucinated
            DraftItemIn(item_id=4, qty=1),  # Mysore Pak — 86'd
        ],
    )
    resp = await run_turn(agent_session, AgentChatRequest(message="2 masala dosa please"))
    assert [i.name for i in resp.draft.items] == ["Masala Dosa"]
    assert resp.draft.items[0].unit_price == Decimal("120.00")
    assert resp.draft.subtotal == Decimal("240.00")
    assert len(resp.warnings) == 2
    assert resp.ready_to_place is False


async def test_ready_gated_by_model_confirmation(agent_session, fake_llm):
    fake_llm["turn"] = AgentTurn(
        reply="Placing it!", draft_items=[DraftItemIn(item_id=1, qty=1)], ready_to_place=True
    )
    resp = await run_turn(agent_session, AgentChatRequest(message="confirm my order"))
    assert resp.ready_to_place is True
    assert resp.kitchen_open is True


async def test_paused_kitchen_blocks_ready(agent_session, fake_llm):
    await agent_session.execute(text("UPDATE settings SET kitchen_paused = true"))
    await agent_session.commit()
    fake_llm["turn"] = AgentTurn(
        reply="Order placed!", draft_items=[DraftItemIn(item_id=1, qty=1)], ready_to_place=True
    )
    resp = await run_turn(agent_session, AgentChatRequest(message="place it"))
    assert resp.ready_to_place is False  # guardrail overrules the model
    assert resp.kitchen_open is False
    assert any("closed" in w for w in resp.warnings)
    payload = _context_payload(fake_llm)
    assert payload["kitchen"] == {"open": False, "paused": True}


async def test_context_carries_menu_prefs_and_draft(agent_session, fake_llm):
    prior_draft = OrderDraft(
        items=[OrderDraftItem(item_id=1, name="Masala Dosa", qty=2, unit_price=Decimal("120.00"))],
        subtotal=Decimal("240.00"),
    )
    await run_turn(
        agent_session,
        AgentChatRequest(
            message="add a filter coffee",
            history=[AgentMessage(role="user", content="2 masala dosa")],
            draft=prior_draft,
            user_id=7,
        ),
    )
    payload = _context_payload(fake_llm)
    names = {m["name"] for m in payload["menu"]}
    assert {"Masala Dosa", "Mysore Pak", "Filter Coffee"} <= names
    sold_out = next(m for m in payload["menu"] if m["name"] == "Mysore Pak")
    assert sold_out["available"] is False  # flagged, not hidden
    assert payload["preferences"]["allergens"] == ["milk"]
    assert payload["current_draft"]["items"][0]["name"] == "Masala Dosa"


async def test_allergen_conflict_from_db_prefs(agent_session, fake_llm):
    fake_llm["turn"] = AgentTurn(
        reply="One filter coffee!", draft_items=[DraftItemIn(item_id=3, qty=1)]
    )
    resp = await run_turn(agent_session, AgentChatRequest(message="one coffee", user_id=7))
    assert [i.name for i in resp.draft.items] == ["Filter Coffee"]
    assert any("milk" in w for w in resp.warnings)


async def test_phone_redacted_in_all_messages(agent_session, fake_llm):
    await run_turn(
        agent_session,
        AgentChatRequest(
            message="call me at +91 98765 43210",
            history=[AgentMessage(role="user", content="my number is 09876543210")],
        ),
    )
    sent = json.dumps(fake_llm["kwargs"]["messages"])
    assert "98765" not in sent and "0987654" not in sent  # Hard Rule 8
    assert "[phone]" in sent
