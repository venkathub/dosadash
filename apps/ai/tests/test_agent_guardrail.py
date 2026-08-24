"""Hard Rule 2 guardrail units — pure logic over a hand-built context."""

from decimal import Decimal

from dosadash_ai.agent.context import AgentContext, MenuItemCtx, UserPrefs
from dosadash_ai.agent.guardrail import gate_ready, validate_draft
from dosadash_shared import DraftItemIn, OrderDraft, OrderDraftItem


def _item(
    item_id: int, name: str, price: str, *, available: bool = True, allergens=()
) -> MenuItemCtx:
    return MenuItemCtx(
        id=item_id,
        name=name,
        category="Dosa",
        price=Decimal(price),
        is_veg=True,
        contains_onion_garlic=True,
        spice_level=1,
        is_available=available,
        schedule=None,
        description=None,
        allergens=tuple(allergens),
    )


def _ctx(**kwargs) -> AgentContext:
    items = {
        1: _item(1, "Masala Dosa", "120"),
        2: _item(2, "Filter Coffee", "60", allergens=("milk",)),
        3: _item(3, "Mysore Pak", "100", available=False),
    }
    return AgentContext(items=items, **kwargs)


def test_hallucinated_item_id_is_stripped():
    draft, warnings = validate_draft(_ctx(), [DraftItemIn(item_id=999, qty=1)])
    assert draft.items == []
    assert any("not on our menu" in w for w in warnings)


def test_unavailable_item_is_stripped_with_name():
    draft, warnings = validate_draft(_ctx(), [DraftItemIn(item_id=3, qty=1)])
    assert draft.items == []
    assert any("Mysore Pak" in w and "not available" in w for w in warnings)


def test_off_window_item_stripped_with_serving_hint(monkeypatch):
    """Phase 11: a dish outside its serving window is removed with WHEN it
    is served — never a bare refusal ('Dosa is not available in Lunch')."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dosadash_shared import availability

    tiffin = {
        d: [{"start": "06:00", "end": "11:30"}, {"start": "17:00", "end": "22:00"}]
        for d in availability.WEEKDAYS
    }
    lunch = datetime(2026, 8, 20, 13, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(availability, "now_ist", lambda: lunch)
    items = {1: _item(1, "Masala Dosa", "120")}
    scheduled = MenuItemCtx(**{**items[1].__dict__, "schedule": tiffin})
    ctx = AgentContext(items={1: scheduled})

    draft, warnings = validate_draft(ctx, [DraftItemIn(item_id=1, qty=1)])
    assert draft.items == []
    assert any("Masala Dosa" in w and "served 6–11:30 AM & 5–10 PM" in w for w in warnings)


def test_menu_payload_and_serving_notes_contract(monkeypatch):
    """Phase 11 contract: presence = orderability — off-window/86'd dishes
    leave the menu payload entirely, and serving_notes computes the
    deterministic reply note (window text for scheduled dishes, sold-out
    for 86'd ones) from the customer's message or attempted draft ids."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dosadash_ai.agent.context import menu_payload
    from dosadash_ai.agent.guardrail import serving_notes
    from dosadash_shared import availability

    tiffin = {
        d: [{"start": "06:00", "end": "11:30"}, {"start": "17:00", "end": "22:00"}]
        for d in availability.WEEKDAYS
    }
    lunch = datetime(2026, 8, 20, 13, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(availability, "now_ist", lambda: lunch)
    base = _ctx().items
    scheduled = MenuItemCtx(**{**base[1].__dict__, "schedule": tiffin})
    ctx = AgentContext(items={1: scheduled, 2: base[2], 3: base[3]})

    menu = {p["name"]: p for p in menu_payload(ctx)}
    assert set(menu) == {"Filter Coffee"}  # only the orderable dish remains
    assert menu["Filter Coffee"]["available"] is True

    # message names the off-window dish → deterministic window note
    notes = serving_notes(ctx, "one masala dosa and a filter coffee please")
    assert notes == ["Masala Dosa is served 6–11:30 AM & 5–10 PM — not right now."]
    # 86'd dish (no window) → sold-out note
    assert serving_notes(ctx, "add a mysore pak") == ["Mysore Pak is sold out right now."]
    # nothing non-orderable mentioned → no notes at all
    assert serving_notes(ctx, "just the coffee thanks") == []
    # model attempted the off-window id without naming it → still noted
    assert serving_notes(ctx, "hmm", attempted_ids=(1,)) == [
        "Masala Dosa is served 6–11:30 AM & 5–10 PM — not right now."
    ]


def test_valid_items_get_db_name_and_price():
    draft, warnings = validate_draft(_ctx(), [DraftItemIn(item_id=1, qty=2, notes="less spicy")])
    assert warnings == []
    assert draft.items == [
        OrderDraftItem(
            item_id=1, name="Masala Dosa", qty=2, unit_price=Decimal("120"), notes="less spicy"
        )
    ]
    assert draft.subtotal == Decimal("240")


def test_duplicate_lines_are_merged():
    draft, _ = validate_draft(
        _ctx(), [DraftItemIn(item_id=1, qty=2), DraftItemIn(item_id=1, qty=1)]
    )
    assert len(draft.items) == 1
    assert draft.items[0].qty == 3


def test_mixed_valid_and_invalid():
    draft, warnings = validate_draft(
        _ctx(),
        [
            DraftItemIn(item_id=1, qty=1),
            DraftItemIn(item_id=999, qty=1),
            DraftItemIn(item_id=3, qty=2),
        ],
    )
    assert [i.item_id for i in draft.items] == [1]
    assert len(warnings) == 2


def test_allergen_conflict_warns_but_keeps_item():
    ctx = _ctx(prefs=UserPrefs(allergens=("milk",)))
    draft, warnings = validate_draft(ctx, [DraftItemIn(item_id=2, qty=1)])
    assert [i.item_id for i in draft.items] == [2]  # warn, don't block
    assert any("Filter Coffee" in w and "milk" in w for w in warnings)


def test_gate_ready_requires_everything():
    open_ctx = _ctx()
    draft, _ = validate_draft(open_ctx, [DraftItemIn(item_id=1, qty=1)])
    assert gate_ready(open_ctx, draft, True) is True
    assert gate_ready(open_ctx, draft, False) is False  # model didn't confirm
    assert gate_ready(open_ctx, OrderDraft(), True) is False  # empty draft
    paused_ctx = _ctx(kitchen_paused=True)
    assert gate_ready(paused_ctx, draft, True) is False  # paused kitchen


def test_drop_substitutions_guards_sold_out_siblings():
    """Phase 11: 86'd Masala Dosa + message '2 masala dosas' must not let a
    drafted Mysore Masala Dosa slip through as a silent substitute — unless
    the customer actually named the sibling too."""
    from dosadash_ai.agent.guardrail import drop_substitutions

    items = {
        1: _item(1, "Masala Dosa", "120", available=False),  # 86'd
        2: _item(2, "Mysore Masala Dosa", "140"),
        3: _item(3, "Filter Coffee", "60"),
    }
    ctx = AgentContext(items=items)
    draft, _ = validate_draft(ctx, [DraftItemIn(item_id=2, qty=2), DraftItemIn(item_id=3, qty=1)])

    kept, warnings = drop_substitutions(ctx, "2 masala dosas please", draft)
    assert [i.name for i in kept.items] == ["Filter Coffee"]  # substitute dropped
    assert kept.subtotal == Decimal("60")
    assert any("Mysore Masala Dosa" in w and "Masala Dosa" in w for w in warnings)

    # customer explicitly named the sibling → it stays
    kept2, warnings2 = drop_substitutions(ctx, "one mysore masala dosa please", draft)
    assert [i.name for i in kept2.items] == ["Mysore Masala Dosa", "Filter Coffee"]
    assert warnings2 == []

    # nothing non-orderable mentioned → untouched
    kept3, warnings3 = drop_substitutions(ctx, "a coffee and a mysore masala dosa", draft)
    assert kept3 is draft and warnings3 == []


def test_reply_draft_contradictions_detects_wrong_refusals():
    """Phase 11 self-correction trigger: orderable dish named by the
    customer + named in the reply + missing from draft + refusal phrasing
    → flagged. Factual answers and honest turns never trigger."""
    from dosadash_ai.agent.guardrail import reply_draft_contradictions
    from dosadash_shared import AgentTurn

    ctx = _ctx()  # Masala Dosa + Filter Coffee orderable, Mysore Pak 86'd

    wrong = AgentTurn(reply="Sorry, Filter Coffee is not available right now.", draft_items=[])
    assert reply_draft_contradictions(ctx, "one filter coffee please", wrong) == ["Filter Coffee"]

    unkept = AgentTurn(reply="I can add the Filter Coffee for you. Anything else?", draft_items=[])
    assert reply_draft_contradictions(ctx, "a filter coffee", unkept) == ["Filter Coffee"]

    honest = AgentTurn(
        reply="Added one Filter Coffee!", draft_items=[DraftItemIn(item_id=2, qty=1)]
    )
    assert reply_draft_contradictions(ctx, "a filter coffee", honest) == []

    # 86'd dish correctly refused → NOT a contradiction (it isn't orderable)
    sold_out = AgentTurn(reply="Mysore Pak is sold out right now.", draft_items=[])
    assert reply_draft_contradictions(ctx, "one mysore pak", sold_out) == []

    # factual answer without refusal phrasing → no trigger
    factual = AgentTurn(reply="Filter Coffee contains milk and sugar.", draft_items=[])
    assert reply_draft_contradictions(ctx, "does filter coffee have milk?", factual) == []


def test_reply_draft_contradictions_substitution_signature():
    """Draft-level trigger: named orderable dish missing while an
    unrequested same-category dish was drafted — but never on removal/
    replacement turns."""
    from dosadash_ai.agent.guardrail import reply_draft_contradictions
    from dosadash_shared import AgentTurn

    items = {
        1: _item(1, "Mutton Chukka", "280"),
        2: _item(2, "Nattu Kozhi Kuzhambu", "260"),
        3: _item(3, "Filter Coffee", "60"),
    }
    ctx = AgentContext(items=items)

    substituted = AgentTurn(
        reply="Added Nattu Kozhi Kuzhambu!", draft_items=[DraftItemIn(item_id=2, qty=1)]
    )
    assert reply_draft_contradictions(ctx, "one mutton chukka please", substituted) == [
        "Mutton Chukka"
    ]

    # customer named both dishes → no trigger
    both = reply_draft_contradictions(ctx, "mutton chukka and nattu kozhi kuzhambu", substituted)
    assert both == []

    # replacement turn: named-but-absent is the CORRECT outcome → suppressed
    replaced = AgentTurn(reply="Swapped it!", draft_items=[DraftItemIn(item_id=2, qty=1)])
    assert (
        reply_draft_contradictions(
            ctx, "make it nattu kozhi instead of the mutton chukka", replaced
        )
        == []
    )
