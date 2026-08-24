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


def _non_orderable_hits(ctx: AgentContext, text: str) -> dict[int, tuple[str, str]]:
    """item_id -> (matched key, canonical name) for every non-orderable dish
    the customer's message names (canonical name minus pack-size, or an
    approved alias)."""
    hits: dict[int, tuple[str, str]] = {}
    for item in ctx.items.values():
        if item.orderable:
            continue
        base = item.name.split(" (")[0].casefold()
        keys = {base} | {a.casefold() for a in item.aliases}
        matched = next((k for k in sorted(keys, key=len, reverse=True) if k and k in text), None)
        if matched:
            hits[item.id] = (matched, item.name)
    return hits


def drop_substitutions(
    ctx: AgentContext, message: str, draft: OrderDraft
) -> tuple[OrderDraft, list[str]]:
    """Anti-substitution guard (Phase 11): when the customer named a dish
    that is off-window/86'd, the model — which only sees orderable dishes —
    sometimes 'resolves' the name onto a similar-sounding sibling (86'd
    Masala Dosa → drafts Mysore Masala Dosa). Deterministic rule: drop any
    drafted dish whose name contains a non-orderable dish's matched key,
    unless the customer actually named the drafted dish too."""
    text = message.casefold()
    hits = _non_orderable_hits(ctx, text)
    if not hits:
        return draft, []
    keys = {key for key, _ in hits.values()}
    kept: list[OrderDraftItem] = []
    warnings: list[str] = []
    for line in draft.items:
        line_base = line.name.split(" (")[0].casefold()
        offending = next(
            (k for k in keys if k in line_base and line_base != k and line_base not in text),
            None,
        )
        if offending:
            wanted = next(name for key, name in hits.values() if key == offending)
            warnings.append(
                f"Removed {line.name} — you asked for {wanted}, which isn't served right now."
            )
            continue
        kept.append(line)
    if not warnings:
        return draft, []
    subtotal = sum((i.unit_price * i.qty for i in kept), Decimal("0"))
    return OrderDraft(items=kept, subtotal=subtotal), warnings


def serving_notes(
    ctx: AgentContext, message: str, attempted_ids: tuple[int, ...] = ()
) -> list[str]:
    """Deterministic serving-window/sold-out notes (Phase 11).

    The model sees ONLY orderable dishes (every prompt variant that exposed
    off-window items or serving-hours text made gpt-4o-mini hallucinate
    refusals of on-menu dishes in the live gate — measured, not assumed).
    So the model observes a clean menu, and THIS post-pass computes the
    availability story: when the customer's message names a non-orderable
    dish (canonical name or approved alias), or the model attempted to
    draft one, the reply gets an authoritative appended note — 'Masala Dosa
    is served 6–11:30 AM & 5–10 PM — not right now.' Dish-QC philosophy:
    the model observes, the verdict is computed."""
    text = message.casefold()
    hits = _non_orderable_hits(ctx, text)
    for item_id in attempted_ids:
        item = ctx.items.get(item_id)
        if item is not None and not item.orderable and item.id not in hits:
            hits[item.id] = (item.name.casefold(), item.name)
    # prefer the most specific mention: drop a hit whose key is a strict
    # substring of another hit's key ("idli" loses to "podi idli")
    keep = {
        item_id
        for item_id, (key, _) in hits.items()
        if not any(key != other and key in other for other, _ in hits.values())
    }
    grouped: dict[str, list[str]] = {}  # note label -> dish names
    for item_id in keep:
        item = ctx.items[item_id]
        windows = availability.serving_windows_text(item.schedule) if item.is_available else None
        label = f"is served {windows} — not right now" if windows else "is sold out right now"
        grouped.setdefault(label, []).append(hits[item_id][1])
    return [f"{' and '.join(sorted(names))} {label}." for label, names in sorted(grouped.items())][
        :3
    ]
