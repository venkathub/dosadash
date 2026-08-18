"""Invoice ↔ purchase-order matching (Phase 6) — deterministic, unit-tested.

The VLM only reads the photo; this module does the reconciliation:
fuzzy-match invoice lines to PO items by name, check delivered quantities
against ordered quantities (±10%), and score the whole invoice against every
PO that is APPROVED (goods expected). The score feeds the confidence gate —
it never moves stock by itself.
"""

from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.db.models import PurchaseOrder, PurchaseOrderItem
from dosadash_shared import (
    InvoiceExtraction,
    InvoiceMatch,
    InvoiceMatchLine,
    POState,
)

NAME_MATCH_FLOOR = 0.55  # below this a PO line counts as missing
QTY_TOLERANCE = Decimal("0.10")  # ±10% delivered vs ordered


def _normalize(name: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def name_score(a: str, b: str) -> float:
    """Max of sequence similarity and token containment. Containment (overlap
    over the SMALLER token set) handles supplier naming noise: "Rice Idli
    Grade-A" fully contains "idli rice" regardless of word order or grade
    suffixes, while disjoint items ("saffron" vs "idli rice") stay at 0."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    containment = len(ta & tb) / min(len(ta), len(tb))
    return round(max(seq, containment), 3)


def _qty_ok(ordered: Decimal, delivered: Decimal) -> bool:
    if ordered <= 0:
        return False
    return abs(delivered - ordered) / ordered <= QTY_TOLERANCE


def match_against_po(extraction: InvoiceExtraction, po: PurchaseOrder) -> InvoiceMatch:
    """Greedy best-name assignment of invoice lines to PO items.

    Score = 0.2·supplier + 0.6·line coverage (name×qty) + 0.2·no-extras.
    """
    supplier_score = (
        name_score(extraction.supplier_name, po.supplier.name)
        if extraction.supplier_name and po.supplier
        else 0.0
    )

    remaining = list(extraction.lines)
    line_matches: list[InvoiceMatchLine] = []
    per_line_scores: list[float] = []
    for item in po.items:
        best, best_score = None, 0.0
        for line in remaining:
            score = name_score(item.ingredient.name, line.name)
            if score > best_score:
                best, best_score = line, score
        if best is None or best_score < NAME_MATCH_FLOOR:
            line_matches.append(
                InvoiceMatchLine(
                    po_ingredient_id=item.ingredient_id,
                    po_ingredient_name=item.ingredient.name,
                    po_qty=item.qty,
                )
            )
            per_line_scores.append(0.0)
            continue
        remaining.remove(best)
        qty_ok = _qty_ok(item.qty, best.qty)
        line_matches.append(
            InvoiceMatchLine(
                po_ingredient_id=item.ingredient_id,
                po_ingredient_name=item.ingredient.name,
                po_qty=item.qty,
                invoice_name=best.name,
                invoice_qty=best.qty,
                name_score=best_score,
                qty_ok=qty_ok,
            )
        )
        per_line_scores.append(best_score * (1.0 if qty_ok else 0.5))

    coverage = sum(per_line_scores) / len(per_line_scores) if per_line_scores else 0.0
    extras = [line.name for line in remaining]
    extras_score = 1.0 if not extras else max(0.0, 1.0 - 0.25 * len(extras))

    score = round(0.2 * supplier_score + 0.6 * coverage + 0.2 * extras_score, 3)
    return InvoiceMatch(
        po_id=po.id,
        supplier_score=supplier_score,
        line_matches=line_matches,
        extra_invoice_lines=extras,
        score=score,
    )


async def find_best_match(
    session: AsyncSession, extraction: InvoiceExtraction
) -> InvoiceMatch | None:
    """Best-scoring APPROVED PO (goods expected), or None when nothing is open."""
    pos = (
        await session.scalars(
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.ingredient),
                selectinload(PurchaseOrder.supplier),
            )
            .where(PurchaseOrder.status == POState.APPROVED)
        )
    ).all()
    if not pos:
        return None
    matches = [match_against_po(extraction, po) for po in pos]
    return max(matches, key=lambda m: m.score)
