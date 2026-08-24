"""The non-negotiable order guardrail (Hard Rule 2).

Every item_id the LLM emits is validated against the DB snapshot before it
reaches a draft: unknown ids are dropped (zero hallucinated dishes), 86'd /
off-schedule items are dropped with an explanation, names and prices are
taken from the DB — never from the model. `ready_to_place` is a guarded
conjunction, not a model opinion.
"""

from decimal import Decimal
from typing import Any

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


def _normalize(text: str) -> str:
    """Collapse repeated letters for typo-tolerant name matching
    ('meddu vadai' → 'medu vadai', which contains 'medu vada')."""
    out: list[str] = []
    for ch in text:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _named_in(text: str, base: str) -> bool:
    return base in text or base in _normalize(text)


def _non_orderable_hits(ctx: AgentContext, text: str) -> dict[int, tuple[str, str]]:
    """item_id -> (matched key, canonical name) for every non-orderable dish
    the customer's message names (canonical name minus pack-size, or an
    approved alias). A key that only occurs inside a LONGER orderable dish
    mention is not a hit — '2 mysore masala dosas' names Mysore Masala
    Dosa, not the 86'd Masala Dosa."""
    orderable_mentions = [
        base
        for item in ctx.items.values()
        if item.orderable and (base := item.name.split(" (")[0].casefold()) and base in text
    ]
    hits: dict[int, tuple[str, str]] = {}
    for item in ctx.items.values():
        if item.orderable:
            continue
        base = item.name.split(" (")[0].casefold()
        keys = {base} | {a.casefold() for a in item.aliases}
        matched = next((k for k in sorted(keys, key=len, reverse=True) if k and k in text), None)
        if matched and any(matched != m and matched in m for m in orderable_mentions):
            continue  # shadowed by a longer orderable-dish mention
        if matched:
            hits[item.id] = (matched, item.name)
    return hits


def drop_substitutions(
    ctx: AgentContext,
    message: str,
    draft: OrderDraft,
    prior_ids: frozenset[int] = frozenset(),
) -> tuple[OrderDraft, list[str]]:
    """Anti-substitution guard (Phase 11): when the customer named a dish
    that is off-window/86'd, the model — which only sees orderable dishes —
    sometimes 'resolves' the name onto a similar-sounding sibling (86'd
    Masala Dosa → drafts Mysore Masala Dosa), or invents an unrequested
    consolation dish ('oru filter coffee venum' while 86'd → drafts Plain
    Dosa). Deterministic rules, active ONLY when the message names a
    non-orderable dish:

    1. drop any drafted dish whose name contains the non-orderable dish's
       matched key, unless the customer actually named it too
    2. drop any drafted dish the customer neither named (typo-normalized)
       nor already had in the incoming draft — suppressed on usual/memory
       and suggest-for-me turns, where unnamed additions are legitimate."""
    text = message.casefold()
    hits = _non_orderable_hits(ctx, text)
    if not hits:
        return draft, []
    keys = {key for key, _ in hits.values()}
    consolation_ok = any(w in text for w in _UNNAMED_DRAFT_OK_WORDS)
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
        last_word = line_base.split()[-1]
        partially_named = len(last_word) >= 4 and _named_in(text, last_word)
        alias_named = any(
            _named_in(text, a.casefold())
            for a in (ctx.items[line.item_id].aliases if line.item_id in ctx.items else ())
        )
        if (
            not consolation_ok
            and line.item_id not in prior_ids
            and not _named_in(text, line_base)
            # partial references count: "a coffee" keeps Filter Coffee;
            # approved translated aliases count too ("மட்டன் சுக்கா")
            and not partially_named
            and not alias_named
        ):
            warnings.append(f"Removed {line.name} — it wasn't part of your request.")
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


_REFUSAL_PHRASES = (
    "not available",
    "unavailable",
    "don't have",
    "do not have",
    "sold out",
    "not serving",
    "not on the menu",
    "not on our menu",
    "nahi hai",
    "illa",
    "கிடைக்கவில்லை",
    "கிடைக்காது",
    "couldn't find",
    "could not find",
    "can't find",
    "cannot find",
)
_UNKEPT_PROMISE_PHRASES = (
    "i can add",
    "i'll add",
    "i will add",
    "however, i can add",
)


_REMOVAL_WORDS = (
    "remove",
    "cancel",
    "hata",
    "nikal",
    "venaam",
    "vendam",
    "venda",
    "without",
    "drop the",
    "no more",
    "rehne do",
    "instead",
)

# Turns where an unnamed drafted dish is legitimate (model chooses for the
# customer, or long-term memory supplies the items).
_UNNAMED_DRAFT_OK_WORDS = (
    "usual",
    "same as",
    "last time",
    "hamesha",
    "vazhakkam",
    "suggest",
    "recommend",
    "surprise",
    "your choice",
    "pick",
    "kuch bhi",
    "anything",
)


def reply_draft_contradictions(ctx: AgentContext, message: str, turn: Any) -> list[str]:
    """Names of ORDERABLE dishes the customer asked for that the model's
    reply wrongly refused ('Curd Rice is not available') or promised but
    didn't draft ('I can add the Filter Coffee') — the trigger for the
    one-round self-correction retry (copilot precedent). Deliberately
    narrow: the dish must be orderable, named in the customer's message,
    named in the reply, absent from draft_items, AND the reply must
    contain a refusal / unkept-promise phrase — factual answers and
    removal turns never trigger.

    Second trigger (draft-level evidence, no phrase needed): the named
    orderable dish is missing from the draft while an UNREQUESTED dish of
    the same category was drafted — the silent-substitution signature
    ('mutton chukka' → drafts Nattu Kozhi Kuzhambu). Suppressed when the
    message carries removal/replacement intent, where a named-but-absent
    dish is the CORRECT outcome.

    Dish mentions match the canonical name OR any approved translated
    alias (post-deploy hotfix: a Tamil 'மட்டன் சுக்கா' order was refused
    ~50% of turns and the trigger never fired because it only knew the
    English name)."""

    def _keys(item: Any) -> list[str]:
        return [item.name.split(" (")[0].casefold(), *(a.casefold() for a in item.aliases)]

    text = message.casefold()
    reply = turn.reply.casefold()
    removal_intent = any(w in text for w in _REMOVAL_WORDS)
    drafted_ids = {line.item_id for line in turn.draft_items}
    drafted_unrequested_categories = {
        ctx.items[i].category
        for i in drafted_ids
        if i in ctx.items and not any(_named_in(text, k) for k in _keys(ctx.items[i]) if k)
    }
    phrase_mode = any(p in reply for p in _REFUSAL_PHRASES + _UNKEPT_PROMISE_PHRASES)
    wrongly_refused = []
    for item in ctx.items.values():
        if not item.orderable or item.id in drafted_ids:
            continue
        keys = [k for k in _keys(item) if k]
        if not any(_named_in(text, k) for k in keys):
            continue
        if phrase_mode and any(k in reply for k in keys):
            wrongly_refused.append(item.name)
        elif not removal_intent and item.category in drafted_unrequested_categories:
            wrongly_refused.append(item.name)  # substitution signature
    return sorted(set(wrongly_refused))
