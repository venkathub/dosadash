"""Admin invoice review queue (Phase 6): supplier invoice OCR intake.

Flow: photo upload → ai VLM extraction (+ arithmetic checks) → deterministic
match against APPROVED POs → confidence gate decides MATCHED (pre-checked)
vs PENDING_REVIEW (flagged) — a human ALWAYS approves before stock moves.
Approve = the linked PO goes RECEIVED via po_service (stock in) and the
invoice is archived with full extraction/match provenance.

All mutations audit + publish inventory.* events (Hard Rule 4).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Invoice, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit, invoice_service, po_service
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    INVOICE_AUTO_MATCH_THRESHOLD,
    InvoiceDecisionIn,
    InvoiceExtractIn,
    InvoiceMatch,
    InvoiceOut,
    InvoiceStatus,
    InvoiceUploadIn,
    POState,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/invoices", tags=["admin:invoices"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


def _combined_confidence(extract_confidence: float, match: InvoiceMatch | None) -> float:
    """Extraction quality and PO agreement carry equal weight; no candidate
    PO caps the score at the extraction half (always below the gate)."""
    if match is None:
        return round(0.5 * extract_confidence, 3)
    return round(0.5 * extract_confidence + 0.5 * match.score, 3)


async def _get_invoice(session: AsyncSession, invoice_id: int) -> Invoice:
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.post("", response_model=InvoiceOut, status_code=201)
async def upload_invoice(
    body: InvoiceUploadIn,
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
    admin: User = AdminUser,
) -> InvoiceOut:
    try:
        result = await ai.extract_invoice(
            InvoiceExtractIn(
                image_base64=body.image_base64,
                mime_type=body.mime_type,
                session_id=f"admin:{admin.id}",
            )
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Invoice extraction unavailable") from exc
    if result.extraction is None:
        raise HTTPException(
            status_code=422, detail=f"Could not read the invoice: {result.error or 'no data'}"
        )

    match = await invoice_service.find_best_match(session, result.extraction)
    confidence = _combined_confidence(result.confidence, match)
    status = (
        InvoiceStatus.MATCHED
        if confidence >= INVOICE_AUTO_MATCH_THRESHOLD
        else InvoiceStatus.PENDING_REVIEW
    )

    invoice = Invoice(
        status=status,
        po_id=match.po_id if match else None,
        confidence=confidence,
        extraction=result.extraction.model_dump(mode="json"),
        match=match.model_dump(mode="json") if match else None,
        model=result.model,
        prompt_version=result.prompt_version,
        uploaded_by=admin.id,
    )
    session.add(invoice)
    audit.record(
        session,
        actor=admin,
        action="invoice.upload",
        entity="invoice",
        detail={
            "status": status.value,
            "confidence": confidence,
            "po_id": match.po_id if match else None,
            "failed_checks": result.failed_checks,
        },
    )
    await session.commit()
    await events.publish_inventory_event(
        "inventory.invoice_uploaded",
        detail={"invoice_id": invoice.id, "status": status.value, "confidence": confidence},
    )
    return InvoiceOut.model_validate(invoice)


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    session: SessionDep,
    admin: User = AdminUser,
    status: InvoiceStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[InvoiceOut]:
    stmt = select(Invoice).order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Invoice.status == status)
    return [InvoiceOut.model_validate(i) for i in (await session.scalars(stmt)).all()]


@router.post("/{invoice_id}/approve", response_model=InvoiceOut)
async def approve_invoice(
    invoice_id: int,
    body: InvoiceDecisionIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> InvoiceOut:
    """Human sign-off: goods in. The (possibly overridden) PO is marked
    RECEIVED — stock moves through the state machine, never directly."""
    invoice = await _get_invoice(session, invoice_id)
    if invoice.status not in (InvoiceStatus.PENDING_REVIEW, InvoiceStatus.MATCHED):
        raise HTTPException(status_code=409, detail=f"Invoice already {invoice.status.value}")

    po_id = body.po_id or invoice.po_id
    if po_id is None:
        raise HTTPException(status_code=422, detail="No matched PO — supply po_id to approve")
    po = await po_service.get_po(session, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if po.status != POState.APPROVED:
        raise HTTPException(
            status_code=409, detail=f"Purchase order is {po.status.value}, expected APPROVED"
        )

    await po_service.receive(session, po)
    invoice.status = InvoiceStatus.APPROVED
    invoice.po_id = po.id
    invoice.reviewed_by = admin.id
    invoice.review_note = body.note
    audit.record(
        session,
        actor=admin,
        action="invoice.approve",
        entity=f"invoice:{invoice.id}",
        detail={"po_id": po.id, "override": body.po_id is not None},
    )
    await session.commit()
    await events.publish_inventory_event(
        "inventory.invoice_approved", detail={"invoice_id": invoice.id, "po_id": po.id}
    )
    await events.publish_inventory_event("inventory.po_received", detail={"po_id": po.id})
    return InvoiceOut.model_validate(invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceOut)
async def reject_invoice(
    invoice_id: int,
    body: InvoiceDecisionIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> InvoiceOut:
    invoice = await _get_invoice(session, invoice_id)
    if invoice.status not in (InvoiceStatus.PENDING_REVIEW, InvoiceStatus.MATCHED):
        raise HTTPException(status_code=409, detail=f"Invoice already {invoice.status.value}")
    invoice.status = InvoiceStatus.REJECTED
    invoice.reviewed_by = admin.id
    invoice.review_note = body.note
    audit.record(
        session,
        actor=admin,
        action="invoice.reject",
        entity=f"invoice:{invoice.id}",
        detail={"note": body.note},
    )
    await session.commit()
    await events.publish_inventory_event(
        "inventory.invoice_rejected", detail={"invoice_id": invoice.id}
    )
    return InvoiceOut.model_validate(invoice)
