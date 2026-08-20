"""Promo guardrail (Phase 7): re-anchor LLM drafts to mined facts + bands.

Combos — the mined pair set is authoritative (inventory-agent pattern):
- a draft whose item pair was not mined is DROPPED (hallucinated pairing)
- prices are clamped into COMBO_PRICE_BAND × sum-of-parts (a combo must be
  a real deal, never free food)
- mined pairs the LLM ignored are FORCE-ADDED with deterministic names
Coupons — values are clamped hard (PCT_BAND / FLAT_BAND), PCT always gets a
max_discount, FLAT always demands min_subtotal ≥ 2× value, and codes are
normalized + deduped against existing ones. The model writes copy; it
never sets economics.
"""

import re
from decimal import Decimal

from dosadash_shared import (
    COMBO_PRICE_BAND,
    FLAT_BAND,
    MAX_COMBO_SUGGESTIONS,
    MAX_COUPON_SUGGESTIONS,
    PCT_BAND,
    CouponType,
    MinedPair,
    PromoComboSuggestion,
    PromoCouponSuggestion,
    PromoDraftBatch,
    PromoStats,
)

_QUANT = Decimal("0.01")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _combo_from(pair: MinedPair, name: str, price: Decimal, rationale: str) -> PromoComboSuggestion:
    low = (pair.parts_total * COMBO_PRICE_BAND[0]).quantize(_QUANT)
    high = (pair.parts_total * COMBO_PRICE_BAND[1]).quantize(_QUANT)
    return PromoComboSuggestion(
        item_ids=list(pair.item_ids),
        names=list(pair.names),
        name=name.strip()[:120] or _default_combo_name(pair),
        price=_clamp(price.quantize(_QUANT), low, high),
        parts_total=pair.parts_total,
        times_ordered=pair.times_ordered,
        rationale=rationale.strip()[:200],
    )


def _default_combo_name(pair: MinedPair) -> str:
    return f"{pair.names[0]} + {pair.names[1]} Combo"


def _default_combo(pair: MinedPair) -> PromoComboSuggestion:
    return _combo_from(
        pair,
        _default_combo_name(pair),
        (pair.parts_total * Decimal("0.90")),
        f"Ordered together {pair.times_ordered}× in the last 90 days",
    )


def sanitize_combos(batch: PromoDraftBatch, pairs: list[MinedPair]) -> list[PromoComboSuggestion]:
    mined = {tuple(sorted(p.item_ids)): p for p in pairs}
    out: list[PromoComboSuggestion] = []
    seen: set[tuple[int, ...]] = set()
    for draft in batch.combos:
        key = tuple(sorted(draft.item_ids))
        pair = mined.get(key)
        if pair is None or key in seen:  # hallucinated pairing or duplicate → drop
            continue
        out.append(_combo_from(pair, draft.name, draft.price, draft.rationale))
        seen.add(key)
    for key, pair in mined.items():  # omissions force-added, best pairs first
        if len(out) >= MAX_COMBO_SUGGESTIONS:
            break
        if key not in seen:
            out.append(_default_combo(pair))
            seen.add(key)
    return out[:MAX_COMBO_SUGGESTIONS]


def _normalize_code(code: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Z0-9]", "", code.upper())[:20] or "DOSADEAL"
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def sanitize_coupons(batch: PromoDraftBatch, stats: PromoStats) -> list[PromoCouponSuggestion]:
    taken = {c.upper() for c in stats.existing_codes}
    out: list[PromoCouponSuggestion] = []
    for draft in batch.coupons[:MAX_COUPON_SUGGESTIONS]:
        code = _normalize_code(draft.code, taken)
        taken.add(code)
        if draft.type == CouponType.PCT:
            value = _clamp(draft.value, *PCT_BAND).quantize(_QUANT)
            # PCT must be capped; default cap ≈ value% of twice the median AOV
            max_discount = draft.max_discount or (
                stats.median_aov * 2 * value / 100 if stats.median_aov > 0 else Decimal("100")
            )
            max_discount = max_discount.quantize(_QUANT)
            min_subtotal = draft.min_subtotal
        else:
            value = _clamp(draft.value, *FLAT_BAND).quantize(_QUANT)
            max_discount = None
            floor = (value * 2).quantize(_QUANT)  # no free-food coupons
            min_subtotal = max(draft.min_subtotal or Decimal("0"), floor)
        out.append(
            PromoCouponSuggestion(
                code=code,
                type=draft.type,
                value=value,
                max_discount=max_discount,
                min_subtotal=min_subtotal,
                description=draft.description.strip()[:200],
                rationale=draft.rationale.strip()[:200],
            )
        )
    return out


def deterministic_coupon(stats: PromoStats) -> PromoCouponSuggestion:
    """LLM-free fallback: a conservative slow-day percent coupon."""
    code = _normalize_code(f"{stats.slow_day[:3]}15", {c.upper() for c in stats.existing_codes})
    return PromoCouponSuggestion(
        code=code,
        type=CouponType.PCT,
        value=Decimal("15"),
        max_discount=Decimal("75.00"),
        min_subtotal=None,
        description=f"15% off on {stats.slow_day.strip()}s",
        rationale=f"{stats.slow_day.strip()} is the slowest revenue day of the last 90",
    )
