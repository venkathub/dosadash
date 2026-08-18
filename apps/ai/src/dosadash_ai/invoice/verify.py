"""Deterministic verification of a VLM invoice extraction.

The model reads the photo; code does the accounting. Confidence is earned
by passing checks, never asserted by the model — it drives the review-queue
gate (MATCHED vs PENDING_REVIEW), and a human always approves before stock
moves.
"""

from decimal import Decimal

from dosadash_shared import InvoiceExtraction

_REL_TOL = Decimal("0.02")  # 2% — printed rounding / small levies


def _close(a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), Decimal("1"))
    return abs(a - b) / scale <= _REL_TOL


def verify(extraction: InvoiceExtraction) -> tuple[list[str], bool, float]:
    """Returns (failed_checks, arithmetic_ok, confidence 0..1).

    Weights: line arithmetic 0.4 · total reconciliation 0.3 ·
    header completeness 0.3.
    """
    failed: list[str] = []

    priced = [ln for ln in extraction.lines if ln.unit_price is not None and ln.amount is not None]
    line_ok = True
    for ln in priced:
        if not _close(ln.qty * ln.unit_price, ln.amount):  # type: ignore[operator]
            line_ok = False
            failed.append(f"line arithmetic off: {ln.name} ({ln.qty}×{ln.unit_price}≠{ln.amount})")
    if not priced:
        line_ok = False
        failed.append("no priced lines to verify")

    amounts = [ln.amount for ln in extraction.lines if ln.amount is not None]
    total_ok = False
    if extraction.total is not None and amounts:
        total_ok = _close(sum(amounts, Decimal("0")), extraction.total)
        if not total_ok:
            failed.append(f"line sum {sum(amounts, Decimal('0'))} ≠ total {extraction.total}")
    else:
        failed.append("missing total or line amounts")

    header_score = (
        (0.5 if extraction.supplier_name else 0.0)
        + (0.25 if extraction.invoice_number else 0.0)
        + (0.25 if extraction.invoice_date else 0.0)
    )
    if header_score < 1.0:
        failed.append("incomplete header (supplier/number/date)")

    confidence = round(
        0.4 * (1.0 if line_ok else 0.0) + 0.3 * (1.0 if total_ok else 0.0) + 0.3 * header_score, 3
    )
    return failed, line_ok and total_ok, confidence
