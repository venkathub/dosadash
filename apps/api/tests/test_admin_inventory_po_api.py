"""Phase 6: admin purchase-order endpoints + Telegram decision path."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Ingredient, StaffAction, Supplier, User
from dosadash_api.main import app
from dosadash_api.services import po_service
from dosadash_api.services.ai_client import get_ai_client
from dosadash_shared import (
    InventoryDraftResult,
    PODraft,
    PODraftLine,
    Role,
)

POS = "/api/v1/admin/purchase-orders"
DECISION = "/api/v1/internal/po/decision"


async def _login_as(db_session, phone: str, role: Role, tg_user_id: int | None = None) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role, tg_user_id=tg_user_id)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin(db_session):
    return await _login_as(db_session, "+919555560001", Role.ADMIN)


@pytest.fixture
async def seeded_po(db_session):
    """One PENDING_APPROVAL agent PO for idli rice (cost 60, qty 25)."""
    supplier = Supplier(name="Madurai Traders")
    db_session.add(supplier)
    await db_session.flush()
    rice = await db_session.scalar(select(Ingredient).where(Ingredient.name == "idli rice"))
    rice.supplier_id = supplier.id
    rice.cost = Decimal("60")
    await db_session.commit()
    result = InventoryDraftResult(
        coverage_days=7,
        drafts=[
            PODraft(
                supplier_id=supplier.id,
                lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("25"), reason="deficit")],
                rationale="Weekend dosa demand.",
            )
        ],
        model="gpt-4o-mini",
    )
    created, _ = await po_service.persist_agent_drafts(db_session, result)
    await db_session.commit()
    return {"po_id": created[0].id, "rice_id": rice.id, "supplier_id": supplier.id}


class FakeAI:
    def __init__(self, result: InventoryDraftResult) -> None:
        self._result = result

    async def draft_inventory_pos(self, request) -> InventoryDraftResult:
        return self._result


# ------------------------------------------------------------------------ RBAC


async def test_po_rbac(client, db_session):
    assert (await client.get(POS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555560002", Role.KITCHEN_STAFF)
    assert (await client.get(POS, headers=kitchen)).status_code == 403


# ------------------------------------------------------------------- list/edit


async def test_list_and_detail(client, admin, seeded_po):
    listed = (await client.get(POS, headers=admin)).json()
    assert len(listed) == 1
    assert listed[0]["status"] == "PENDING_APPROVAL"
    assert listed[0]["supplier_name"] == "Madurai Traders"
    assert Decimal(listed[0]["expected_cost"]) == Decimal("1500")

    detail = (await client.get(f"{POS}/{seeded_po['po_id']}", headers=admin)).json()
    assert detail["items"][0]["ingredient_name"] == "idli rice"
    assert detail["items"][0]["unit"] == "kg"

    filtered = (await client.get(POS, headers=admin, params={"status": "RECEIVED"})).json()
    assert filtered == []


async def test_edit_line_recomputes_cost_then_locks_after_approval(client, admin, seeded_po):
    po_id, rice_id = seeded_po["po_id"], seeded_po["rice_id"]
    edited = await client.patch(f"{POS}/{po_id}/items/{rice_id}", headers=admin, json={"qty": "10"})
    assert edited.status_code == 200
    assert Decimal(edited.json()["expected_cost"]) == Decimal("600")  # 10 × 60

    assert (await client.post(f"{POS}/{po_id}/approve", headers=admin)).status_code == 200
    locked = await client.patch(f"{POS}/{po_id}/items/{rice_id}", headers=admin, json={"qty": "5"})
    assert locked.status_code == 409


# ------------------------------------------------------------------ transitions


async def test_approve_receive_updates_stock_and_audits(client, admin, seeded_po, db_session):
    po_id, rice_id = seeded_po["po_id"], seeded_po["rice_id"]
    rice = await db_session.get(Ingredient, rice_id)
    stock_before = rice.stock_qty

    # receive before approve → 409
    assert (await client.post(f"{POS}/{po_id}/receive", headers=admin)).status_code == 409

    approved = (await client.post(f"{POS}/{po_id}/approve", headers=admin)).json()
    assert approved["status"] == "APPROVED"
    received = (await client.post(f"{POS}/{po_id}/receive", headers=admin)).json()
    assert received["status"] == "RECEIVED"

    await db_session.refresh(rice)
    assert rice.stock_qty == stock_before + Decimal("25")

    actions = (await db_session.scalars(select(StaffAction.action).order_by(StaffAction.id))).all()
    assert "po.approve" in actions and "po.receive" in actions

    # terminal
    assert (await client.post(f"{POS}/{po_id}/cancel", headers=admin)).status_code == 409


async def test_reject_is_terminal(client, admin, seeded_po):
    po_id = seeded_po["po_id"]
    assert (await client.post(f"{POS}/{po_id}/reject", headers=admin)).json()[
        "status"
    ] == "REJECTED"
    assert (await client.post(f"{POS}/{po_id}/approve", headers=admin)).status_code == 409


# -------------------------------------------------------------------- draft-now


async def test_draft_now_persists_agent_result(client, admin, db_session, monkeypatch):
    monkeypatch.setenv("API_BOT_BASE_URL", "")  # notifications off in tests
    get_settings.cache_clear()
    rice = await db_session.scalar(select(Ingredient).where(Ingredient.name == "idli rice"))
    result = InventoryDraftResult(
        coverage_days=7,
        drafts=[
            PODraft(
                supplier_id=None,
                lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("12"), reason="deficit")],
                rationale="Restock rice.",
            )
        ],
        model="gpt-4o-mini",
    )
    app.dependency_overrides[get_ai_client] = lambda: FakeAI(result)
    try:
        resp = await client.post(f"{POS}/draft-now", headers=admin)
        assert resp.status_code == 200, resp.text
        created = resp.json()
        assert len(created) == 1
        assert created[0]["status"] == "PENDING_APPROVAL"
        assert created[0]["items"][0]["ingredient_name"] == "idli rice"

        # re-run: open PO for same (null) supplier → skipped, no duplicates
        resp2 = await client.post(f"{POS}/draft-now", headers=admin)
        assert resp2.json() == []
    finally:
        app.dependency_overrides.pop(get_ai_client, None)
        get_settings.cache_clear()


# ------------------------------------------------- Telegram decision (internal)


def _internal(monkeypatch) -> dict:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    get_settings.cache_clear()
    return {"X-Internal-Token": "test-internal"}


async def test_telegram_decision_owner_approves(client, db_session, seeded_po, monkeypatch):
    headers = _internal(monkeypatch)
    await _login_as(db_session, "+919555560003", Role.OWNER, tg_user_id=777001)
    resp = await client.post(
        DECISION,
        headers=headers,
        json={"tg_user_id": 777001, "po_id": seeded_po["po_id"], "action": "approve"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["status"] == "APPROVED"


async def test_telegram_decision_rbac_and_auth(client, db_session, seeded_po, monkeypatch):
    headers = _internal(monkeypatch)
    # customer's linked account cannot approve
    await _login_as(db_session, "+919555560004", Role.CUSTOMER, tg_user_id=777002)
    resp = await client.post(
        DECISION,
        headers=headers,
        json={"tg_user_id": 777002, "po_id": seeded_po["po_id"], "action": "approve"},
    )
    assert resp.json()["ok"] is False

    # wrong internal token
    bad = await client.post(
        DECISION,
        headers={"X-Internal-Token": "nope"},
        json={"tg_user_id": 777002, "po_id": seeded_po["po_id"], "action": "approve"},
    )
    assert bad.status_code == 403
