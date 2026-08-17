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
