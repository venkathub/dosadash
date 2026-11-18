"""Deterministic serving regressions from a failed live gate; no provider calls."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from dosadash_ai.agent.context import AgentContext, MenuItemCtx
from dosadash_ai.agent.graph import _llm_turn, build_response
from dosadash_shared import AgentChatRequest, AgentTurn, DraftItemIn


def item(id, name):
    return MenuItemCtx(
        id=id,
        name=name,
        category="Dosa",
        price=Decimal("100"),
        is_veg=True,
        contains_onion_garlic=False,
        spice_level=0,
        is_available=True,
        schedule=None,
        description=None,
    )


@pytest.mark.parametrize(
    "language,phrase",
    [
        ("en", "paused"),
        ("hinglish", "band"),
        ("tanglish", "moodiruku"),
        ("ta", "மூடப்பட்டுள்ளது"),
    ],
)
def test_kitchen_pause_cannot_leave_a_false_confirmation(language, phrase):
    ctx = AgentContext(items={41: item(41, "Set Dosa")}, kitchen_paused=True)
    turn = AgentTurn(
        reply="Order placed!",
        reply_language=language,
        draft_items=[DraftItemIn(item_id=41, qty=2)],
        ready_to_place=True,
    )
    result = build_response(ctx, turn, "stub")
    assert not result.ready_to_place and not result.kitchen_open
    assert phrase in result.reply and "Order placed" not in result.reply
    assert result.draft.items[0].qty == 2  # cart is retained, never placed
    open_result = build_response(replace(ctx, kitchen_paused=False), turn, "stub")
    assert open_result.reply == turn.reply and open_result.ready_to_place


def test_hours_closed_uses_server_state(monkeypatch):
    monkeypatch.setattr("dosadash_shared.availability.is_open", lambda hours: False)
    response = build_response(AgentContext(), AgentTurn(reply="Order placed!"), "stub")
    assert "closed" in response.reply and not response.ready_to_place


async def test_correction_uses_authoritative_ids_not_similar_dish_ids(monkeypatch):
    from dosadash_ai.agent import graph

    calls = []

    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return AgentTurn(
                reply="Added Set Dosa", draft_items=[DraftItemIn(item_id=7, qty=2)]
            ), "stub"
        payload = json.loads(kwargs["messages"][-1]["content"].split("CORRECTION_ITEMS: ")[1])
        assert payload == [{"item_id": 41, "name": "Set Dosa"}]
        assert kwargs["prompt_version"] == "order_correction_v1"
        return AgentTurn(
            reply="Added Set Dosa", draft_items=[DraftItemIn(item_id=41, qty=2)]
        ), "stub"

    monkeypatch.setattr(graph, "structured_completion", completion)
    state = {
        "ctx": AgentContext(items={7: item(7, "Plain Dosa"), 41: item(41, "Set Dosa")}),
        "request": AgentChatRequest(message="two Set Dosa please"),
    }
    result = await _llm_turn(state)
    assert len(calls) == 2 and result["turn"].draft_items[0].item_id == 41
