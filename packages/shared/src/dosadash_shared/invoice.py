"""Supplier invoice OCR schemas (Phase 6): VLM structured extraction → PO
matching → confidence-gated human review queue → stock update.

Hard Rule 3 applies to vision too: the VLM emits `InvoiceExtraction`, never
free text. Matching and arithmetic verification are deterministic code —
the model only reads the photo.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

INVOICE_PROMPT_VERSION = "invoice_extract_v1"

# Combined extraction × match score at/above which an invoice is pre-checked
# ("MATCHED") in the review queue; below it lands flagged ("PENDING_REVIEW").
# Either way a human approves before stock moves — the gate only decides how
# loudly we ask.
INVOICE_AUTO_MATCH_THRESHOLD = 0.8


class InvoiceStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"  # low confidence — needs a careful look
    MATCHED = "MATCHED"  # high confidence — pre-checked, one-click approve
    APPROVED = "APPROVED"  # human approved → PO received, stock updated
    REJECTED = "REJECTED"


class InvoiceLine(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    qty: Decimal = Field(gt=0, le=Decimal("100000"))
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, ge=0)


class InvoiceExtraction(BaseModel):
    """Structured VLM output for one supplier invoice photo."""

    supplier_name: str | None = Field(default=None, max_length=120)
    invoice_number: str | None = Field(default=None, max_length=60)
    invoice_date: str | None = Field(default=None, max_length=20)
    lines: list[InvoiceLine] = Field(min_length=1, max_length=60)
    total: Decimal | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=300)


class InvoiceExtractIn(BaseModel):
    """api → ai. Images only (photo path); PDFs need rasterizing first and
    are out of scope for the 4 GB VPS."""

    image_base64: str = Field(min_length=8, max_length=10_000_000)  # ~7 MB image
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    session_id: str | None = None


class InvoiceExtractResult(BaseModel):
    extraction: InvoiceExtraction | None = None
    model: str | None = None
    prompt_version: str = INVOICE_PROMPT_VERSION
    failed_checks: list[str] = []
    arithmetic_ok: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    error: str | None = None


# ------------------------------------------------------------------- matching


class InvoiceMatchLine(BaseModel):
    po_ingredient_id: int
    po_ingredient_name: str
    po_qty: Decimal
    invoice_name: str | None = None  # None → line missing from the invoice
    invoice_qty: Decimal | None = None
    name_score: float = 0.0
    qty_ok: bool = False


class InvoiceMatch(BaseModel):
    po_id: int
    supplier_score: float = 0.0
    line_matches: list[InvoiceMatchLine] = []
    extra_invoice_lines: list[str] = []  # billed but not on the PO — review!
    score: float = Field(ge=0.0, le=1.0)


# ------------------------------------------------------------------ api layer


class InvoiceUploadIn(BaseModel):
    image_base64: str = Field(min_length=8, max_length=10_000_000)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]


class InvoiceDecisionIn(BaseModel):
    po_id: int | None = None  # approve-time override of the auto-match
    note: str | None = Field(default=None, max_length=300)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: InvoiceStatus
    po_id: int | None = None
    confidence: float
    extraction: InvoiceExtraction | None = None
    match: InvoiceMatch | None = None
    model: str | None = None
    prompt_version: str | None = None
    uploaded_by: int
    reviewed_by: int | None = None
    review_note: str | None = None
    created_at: datetime
