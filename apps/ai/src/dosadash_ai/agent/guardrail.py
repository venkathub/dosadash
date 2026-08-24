"""The non-negotiable order guardrail (Hard Rule 2).

Every item_id the LLM emits is validated against the DB snapshot before it
reaches a draft: unknown ids are dropped (zero hallucinated dishes), 86'd /
off-schedule items are dropped with an explanation, names and prices are
taken from the DB — never from the model. `ready_to_place` is a guarded
conjunction, not a model opinion.
"""

from decimal import Decimal

from dosadash_ai.agent.context import AgentContext
from dosadash_shared import DraftItemIn, OrderDraft, OrderDraftItem, availability


def validate_draft(ctx: AgentContext, proposed: list[DraftItemIn]) -> tuple[OrderDraft, list[str]]:
    """DB-validate the LLM's proposed lines → (authoritative draft, warnings)."""
    items: list[OrderDraftItem] = []
    warnings: list[str] = []
    seen: dict[int, int] = {}  # item_id → index in items (merge duplicates)

    for line in proposed:
        item = ctx.items.get(line.item_id)
        if item is None:
            warnings.append(f"Removed an item that is not on our menu (id {line.item_id}).")
            continue
        if not item.orderable:
            windows = (
                availability.serving_windows_text(item.schedule) if item.is_available else None
            )
            if windows:  # on the menu, just outside its serving window
                warnings.append(f"Removed {item.name} — it is served {windows}.")
            else:  # 86'd (or schedule with no windows today)
                warnings.append(f"Removed {item.name} — it is not available right now.")
            continue
        if item.id in seen:
            merged = items[seen[item.id]]
            items[seen[item.id]] = merged.model_copy(update={"qty": min(20, merged.qty + line.qty)})
            continue
        seen[item.id] = len(items)
        items.append(
            OrderDraftItem(
                item_id=item.id,
                name=item.name,  # DB name, never the model's
                qty=line.qty,
                unit_price=item.price,  # DB price, never the model's
                notes=line.notes,
            )
        )

    if ctx.prefs and ctx.prefs.allergens:
        for draft_item in items:
            conflicts = set(ctx.items[draft_item.item_id].allergens) & set(ctx.prefs.allergens)
            if conflicts:
                warnings.append(
                    f"Heads up: {draft_item.name} contains "
                    f"{', '.join(sorted(conflicts))} (in your allergen list)."
                )

    subtotal = sum((i.unit_price * i.qty for i in items), Decimal("0"))
    return OrderDraft(items=items, subtotal=subtotal), warnings


def gate_ready(ctx: AgentContext, draft: OrderDraft, model_says_ready: bool) -> bool:
    """ready_to_place = model intent AND non-empty valid draft AND open kitchen."""
    return bool(model_says_ready and draft.items and ctx.kitchen_open)
