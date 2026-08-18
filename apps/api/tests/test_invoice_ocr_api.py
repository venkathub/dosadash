"""Phase 6 invoice OCR: PO matching service + review-queue endpoints."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Ingredient, Supplier, User
from dosadash_api.main import app
from dosadash_api.services import invoice_service, po_service
from dosadash_api.services.ai_client import get_ai_client
from dosadash_shared import (
    InventoryDraftResult,
    InvoiceExtraction,
    InvoiceExtractResult,
    InvoiceLine,
    PODraft,
    PODraftLine,
    Role,
)

INVOICES = "/api/v1/admin/invoices"


async def _login_as(db_session, phone: str, role: Role) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin(db_session):
    return await _login_as(db_session, "+919555561001", Role.ADMIN)


async def _approved_po(db_session):
    """APPROVED PO: 25 kg idli rice + 10 l milk from Madurai Traders."""
    supplier = Supplier(name="Madurai Traders")
    db_session.add(supplier)
    owner = User(phone="+919555561009", name="Owner", role=Role.OWNER)
    db_session.add(owner)
    await db_session.flush()
    rice = await db_session.scalar(select(Ingredient).where(Ingredient.name == "idli rice"))
    milk = await db_session.scalar(select(Ingredient).where(Ingredient.name == "milk"))
    rice.supplier_id = supplier.id
    result = InventoryDraftResult(
        coverage_days=7,
        drafts=[
            PODraft(
                supplier_id=supplier.id,
                lines=[
                    PODraftLine(ingredient_id=rice.id, qty=Decimal("25"), reason="deficit"),
                    PODraftLine(ingredient_id=milk.id, qty=Decimal("10"), reason="deficit"),
                ],
                rationale="Restock.",
            )
        ],
    )
    created, _ = await po_service.persist_agent_drafts(db_session, result)
    po = created[0]
    po_service.approve(po, actor_id=owner.id)
    await db_session.commit()
    return po, rice, milk


def _extraction(**overrides) -> InvoiceExtraction:
    defaults = dict(
        supplier_name="Madurai Traders",
        invoice_number="MT-1",
        invoice_date="16/08/2026",
        lines=[
            InvoiceLine(name="Idli Rice Grade A", qty=Decimal("25"), unit="kg"),
            InvoiceLine(name="Milk", qty=Decimal("10"), unit="l"),
        ],
        total=Decimal("2000"),
    )
    defaults.update(overrides)
    return InvoiceExtraction(**defaults)


# ------------------------------------------------------------------- matching


async def test_match_scores_clean_delivery_high(db_session):
    po, _, _ = await _approved_po(db_session)
    match = await invoice_service.find_best_match(db_session, _extraction())
    assert match is not None and match.po_id == po.id
    assert match.score >= 0.85
    assert all(m.qty_ok for m in match.line_matches)
    assert match.extra_invoice_lines == []


async def test_match_flags_short_delivery_and_extras(db_session):
    await _approved_po(db_session)
    extraction = _extraction(
        lines=[
            InvoiceLine(name="Idli Rice", qty=Decimal("15")),  # 25 ordered → 40% short
            InvoiceLine(name="Saffron premium", qty=Decimal("1")),  # never ordered
        ]
    )
    match = await invoice_service.find_best_match(db_session, extraction)
    rice_line = next(m for m in match.line_matches if m.po_ingredient_name == "idli rice")
    assert rice_line.qty_ok is False
    milk_line = next(m for m in match.line_matches if m.po_ingredient_name == "milk")
    assert milk_line.invoice_name is None  # missing from invoice
    assert match.extra_invoice_lines == ["Saffron premium"]
    assert match.score < 0.7


async def test_match_none_when_no_approved_pos(db_session):
    assert await invoice_service.find_best_match(db_session, _extraction()) is None


def test_name_score_handles_word_order_and_noise():
    assert invoice_service.name_score("Idli Rice Grade A", "idli rice") >= 0.55
    assert invoice_service.name_score("Saffron", "idli rice") < 0.3


# ------------------------------------------------------------------ endpoints


class FakeAI:
    def __init__(self, result: InvoiceExtractResult) -> None:
        self._result = result

    async def extract_invoice(self, request) -> InvoiceExtractResult:
        return self._result


def _fake_ai(extraction: InvoiceExtraction | None, confidence: float = 1.0) -> FakeAI:
    return FakeAI(
        InvoiceExtractResult(
            extraction=extraction,
            model="gpt-4o-mini",
            confidence=confidence,
            arithmetic_ok=confidence >= 0.8,
            error=None if extraction else "unreadable",
        )
    )


UPLOAD = {"image_base64": "aGVsbG8=", "mime_type": "image/jpeg"}


async def test_upload_high_confidence_lands_matched_then_approve_moves_stock(
    client, admin, db_session
):
    po, rice, milk = await _approved_po(db_session)
    rice_before, milk_before = rice.stock_qty, milk.stock_qty
    app.dependency_overrides[get_ai_client] = lambda: _fake_ai(_extraction(), confidence=1.0)
    try:
        resp = await client.post(INVOICES, headers=admin, json=UPLOAD)
        assert resp.status_code == 201, resp.text
        invoice = resp.json()
        assert invoice["status"] == "MATCHED"
        assert invoice["po_id"] == po.id
        assert invoice["confidence"] >= 0.8

        approved = await client.post(f"{INVOICES}/{invoice['id']}/approve", headers=admin, json={})
        assert approved.status_code == 200
        assert approved.json()["status"] == "APPROVED"

        await db_session.refresh(rice)
        await db_session.refresh(milk)
        assert rice.stock_qty == rice_before + Decimal("25")
        assert milk.stock_qty == milk_before + Decimal("10")

        # PO now RECEIVED → double approve is blocked
        again = await client.post(f"{INVOICES}/{invoice['id']}/approve", headers=admin, json={})
        assert again.status_code == 409
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_upload_low_confidence_lands_pending_review(client, admin, db_session):
    await _approved_po(db_session)
    bad = _extraction(supplier_name=None, lines=[InvoiceLine(name="???", qty=Decimal("1"))])
    app.dependency_overrides[get_ai_client] = lambda: _fake_ai(bad, confidence=0.3)
    try:
        resp = await client.post(INVOICES, headers=admin, json=UPLOAD)
        assert resp.status_code == 201
        assert resp.json()["status"] == "PENDING_REVIEW"

        # approve without a PO match → must supply po_id explicitly
        invoice_id = resp.json()["id"]
        if resp.json()["po_id"] is None:
            missing = await client.post(f"{INVOICES}/{invoice_id}/approve", headers=admin, json={})
            assert missing.status_code == 422
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_upload_unreadable_photo_422_and_reject_flow(client, admin, db_session):
    await _approved_po(db_session)
    app.dependency_overrides[get_ai_client] = lambda: _fake_ai(None)
    try:
        resp = await client.post(INVOICES, headers=admin, json=UPLOAD)
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_ai_client, None)

    app.dependency_overrides[get_ai_client] = lambda: _fake_ai(_extraction(), confidence=0.4)
    try:
        invoice = (await client.post(INVOICES, headers=admin, json=UPLOAD)).json()
        rejected = await client.post(
            f"{INVOICES}/{invoice['id']}/reject", headers=admin, json={"note": "duplicate bill"}
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "REJECTED"
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_invoice_rbac(client, db_session):
    assert (await client.get(INVOICES)).status_code == 401
    kitchen = await _login_as(db_session, "+919555561002", Role.KITCHEN_STAFF)
    assert (await client.get(INVOICES, headers=kitchen)).status_code == 403
