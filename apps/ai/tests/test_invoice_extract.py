"""Invoice OCR ai-side tests: arithmetic verifier + VLM message shape."""

from decimal import Decimal

from dosadash_ai.invoice.extract import VISION_MODELS, build_messages
from dosadash_ai.invoice.verify import verify
from dosadash_shared import InvoiceExtractIn, InvoiceExtraction, InvoiceLine


def _line(name: str, qty: str, price: str | None, amount: str | None) -> InvoiceLine:
    return InvoiceLine(
        name=name,
        qty=Decimal(qty),
        unit="kg",
        unit_price=Decimal(price) if price else None,
        amount=Decimal(amount) if amount else None,
    )


def test_verify_clean_invoice_scores_high():
    extraction = InvoiceExtraction(
        supplier_name="Madurai Traders",
        invoice_number="MT-2417",
        invoice_date="16/08/2026",
        lines=[_line("Idli Rice", "25", "62", "1550"), _line("Urad Dal", "10", "140", "1400")],
        total=Decimal("2950"),
    )
    failed, arithmetic_ok, confidence = verify(extraction)
    assert failed == []
    assert arithmetic_ok is True
    assert confidence == 1.0


def test_verify_flags_bad_line_arithmetic():
    extraction = InvoiceExtraction(
        supplier_name="Madurai Traders",
        invoice_number="MT-2418",
        invoice_date="17/08/2026",
        lines=[_line("Idli Rice", "25", "62", "9999")],  # 25×62 ≠ 9999
        total=Decimal("9999"),
    )
    failed, arithmetic_ok, confidence = verify(extraction)
    assert any("arithmetic" in f for f in failed)
    assert arithmetic_ok is False
    assert confidence < 0.8  # lands in PENDING_REVIEW territory


def test_verify_flags_total_mismatch_and_missing_header():
    extraction = InvoiceExtraction(
        lines=[_line("Idli Rice", "25", "62", "1550")],
        total=Decimal("9000"),
    )
    failed, arithmetic_ok, confidence = verify(extraction)
    assert any("total" in f for f in failed)
    assert any("header" in f for f in failed)
    assert arithmetic_ok is False
    assert confidence < 0.5


def test_verify_tolerates_two_percent_rounding():
    extraction = InvoiceExtraction(
        supplier_name="S",
        invoice_number="1",
        invoice_date="d",
        lines=[_line("Rice", "25", "62", "1551")],  # 1550 within 2%
        total=Decimal("1551"),
    )
    failed, arithmetic_ok, _ = verify(extraction)
    assert arithmetic_ok is True
    assert failed == []


def test_build_messages_embeds_data_url_and_vision_chain_excludes_groq():
    request = InvoiceExtractIn(image_base64="aGVsbG8=", mime_type="image/jpeg")
    messages = build_messages(request)
    assert messages[0]["role"] == "system"
    image_part = messages[1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,aGVsbG8=")
    assert all("groq" not in m for m in VISION_MODELS)  # text-only model skipped
