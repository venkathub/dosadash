"""Checkout combo/add-on suggester (Phase 7) — pure, deterministic rules.

No LLM anywhere: this is classic basket logic ranked by the recommender's
scores. Two rule families, in priority order:

1. **Combo completion** — an APPROVED combo where the cart already holds all
   but ONE of the items: suggest the missing item ("completes the X combo").
   Highest intent, so it always outranks pairing.
2. **Pairing gap** — the cart lacks classic attach categories (beverage /
   sweet / snack): suggest the highest-scored orderable candidate across ALL
   missing attach categories (at most one per category). Scores come from
   the caller (ALS fold-in for returning users, embedding/popularity
   otherwise), so the recommender picks the category too — a sweet tooth
   gets payasam where a coffee drinker gets filter coffee. (The first cut
   used a fixed category priority; the synthetic A/B sim caught it LOSING
   to random suggestions — personal taste beats merchandising order.)

The module is shared by apps/ai serving and the synthetic A/B simulation —
one implementation, measured and served identically.
"""

from dataclasses import dataclass

# Classic attach categories (docs/06 menu economics). No priority order:
# the recommender's scores decide which gap to fill for THIS customer.
PAIRING_CATEGORIES = frozenset({"Beverages", "Sweets", "Snacks"})
MAX_SUGGESTIONS = 2


@dataclass(frozen=True)
class SuggestCandidate:
    name: str
    category: str
    score: float  # recommender score — higher is better


@dataclass(frozen=True)
class ComboDef:
    name: str
    item_names: tuple[str, ...]


@dataclass(frozen=True)
class Suggestion:
    item_name: str
    kind: str  # "combo" | "pairing"
    reason: str


def suggest_addons(
    cart_names: set[str],
    cart_categories: set[str],
    candidates: list[SuggestCandidate],
    combos: list[ComboDef] = (),
    *,
    max_suggestions: int = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """Deterministic: same inputs → same suggestions. Candidates must already
    be orderable and not in the cart (the caller owns availability truth)."""
    if not cart_names:
        return []
    by_name = {c.name: c for c in candidates}
    suggestions: list[Suggestion] = []
    taken: set[str] = set(cart_names)

    # 1) combo completion
    for combo in combos:
        if len(suggestions) >= max_suggestions:
            break
        if len(combo.item_names) < 2:
            continue
        missing = [n for n in combo.item_names if n not in taken]
        if len(missing) == 1 and missing[0] in by_name:
            suggestions.append(
                Suggestion(
                    item_name=missing[0],
                    kind="combo",
                    reason=f"Completes the {combo.name}",
                )
            )
            taken.add(missing[0])

    # 2) pairing gaps: best-scored candidate across all missing attach
    #    categories, at most one suggestion per category
    gaps = PAIRING_CATEGORIES - cart_categories
    pool = sorted(
        (c for c in candidates if c.category in gaps and c.name not in taken),
        key=lambda c: c.score,
        reverse=True,
    )
    used_categories: set[str] = set()
    for candidate in pool:
        if len(suggestions) >= max_suggestions:
            break
        if candidate.category in used_categories:
            continue
        suggestions.append(
            Suggestion(
                item_name=candidate.name,
                kind="pairing",
                reason=_PAIRING_REASON[candidate.category],
            )
        )
        taken.add(candidate.name)
        used_categories.add(candidate.category)

    return suggestions[:max_suggestions]


_PAIRING_REASON = {
    "Beverages": "Goes well with your order",
    "Sweets": "Finish on a sweet note",
    "Snacks": "A little something on the side",
}
