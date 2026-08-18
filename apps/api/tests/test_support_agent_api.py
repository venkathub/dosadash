"""Phase 6 support agent: api-side execution, guardrails, escalation inbox."""

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Escalation, Order, User
from dosadash_api.main import app
from dosadash_api.services.ai_client import get_ai_client
from dosadash_shared import (
    EscalationStatus,
    OrderState,
    Role,
    SupportAgentResponse,
    SupportTurn,
)

CHAT = "/api/v1/support/chat"
INBOX = "/api/v1/admin/escalations"


async def _customer(client, phone="9111111111") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _admin(db_session) -> dict:
    user = User(phone="+919555562001", name="Admin", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _place_order(client, customer) -> dict:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 2}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class FakeAI:
    def __init__(self, turn: SupportTurn) -> None:
        self._turn = turn
        self.last_request = None

    async def support_chat(self, request) -> SupportAgentResponse:
        self.last_request = request
        return SupportAgentResponse(turn=self._turn, model="gpt-4o-mini")


def _use(turn: SupportTurn) -> FakeAI:
    fake = FakeAI(turn)
    app.dependency_overrides[get_ai_client] = lambda: fake
    return fake


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_ai_client, None)


async def test_chat_requires_auth(client):
    assert (await client.post(CHAT, json={"message": "help"})).status_code in (401, 403)


async def test_get_status_returns_own_order(client, db_session):
    customer = await _customer(client)
    order = await _place_order(client, customer)
    _use(SupportTurn(reply="Here's your order status.", action="get_status", order_id=order["id"]))

    resp = await client.post(CHAT, headers=customer, json={"message": "where is my order?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "get_status"
    assert body["order"]["id"] == order["id"]


async def test_get_status_never_leaks_foreign_orders(client, db_session):
    victim = await _customer(client, phone="9111111111")
    victim_order = await _place_order(client, victim)
    attacker = await _customer(client, phone="9122222222")
    # even if the model somehow emitted a foreign order_id, ownership is
    # re-checked api-side → no order payload
    _use(SupportTurn(reply="Status:", action="get_status", order_id=victim_order["id"]))

    resp = await client.post(CHAT, headers=attacker, json={"message": "status of my order"})
    assert resp.status_code == 200
    assert resp.json()["order"] is None


async def test_cancel_placed_order_goes_through_state_machine(client, db_session):
    customer = await _customer(client)
    order = await _place_order(client, customer)
    _use(SupportTurn(reply="Cancelling that for you.", action="cancel_order", order_id=order["id"]))

    resp = await client.post(CHAT, headers=customer, json={"message": "cancel my order"})
    body = resp.json()
    assert body["order"]["status"] == "CANCELLED"
    assert body["escalation_id"] is None


async def test_cancel_cooking_order_escalates_instead(client, db_session):
    customer = await _customer(client)
    order = await _place_order(client, customer)
    db_order = await db_session.get(Order, order["id"])
    db_order.status = OrderState.COOKING
    await db_session.commit()
    _use(SupportTurn(reply="Let me try.", action="cancel_order", order_id=order["id"]))

    resp = await client.post(CHAT, headers=customer, json={"message": "cancel it now!"})
    body = resp.json()
    assert body["order"] is None
    assert body["escalation_id"] is not None
    assert "can't be cancelled" in body["reply"]


async def test_refund_request_creates_escalation_not_refund(client, db_session):
    customer = await _customer(client)
    order = await _place_order(client, customer)
    _use(
        SupportTurn(
            reply="The team will review this.",
            action="refund_request",
            order_id=order["id"],
            reason="cold food",
        )
    )

    resp = await client.post(CHAT, headers=customer, json={"message": "food was cold, refund!"})
    body = resp.json()
    assert body["escalation_id"] is not None

    escalation = await db_session.get(Escalation, body["escalation_id"])
    assert escalation.kind == "refund"
    assert escalation.status == EscalationStatus.OPEN
    assert escalation.agent_summary == "cold food"
    # the order itself is untouched — no auto-refund, ever
    assert (await client.get(f"/api/v1/orders/{order['id']}", headers=customer)).json()[
        "status"
    ] == "PLACED"


async def test_inbox_rbac_and_resolve_dismiss(client, db_session):
    customer = await _customer(client)
    await _place_order(client, customer)
    _use(SupportTurn(reply="Escalating.", action="escalate", reason="angry"))
    ticket_id = (await client.post(CHAT, headers=customer, json={"message": "complaint!!"})).json()[
        "escalation_id"
    ]

    assert (await client.get(INBOX)).status_code == 401
    assert (await client.get(INBOX, headers=customer)).status_code == 403

    admin = await _admin(db_session)
    inbox = (await client.get(INBOX, headers=admin, params={"status": "OPEN"})).json()
    assert [t["id"] for t in inbox] == [ticket_id]

    resolved = await client.post(
        f"{INBOX}/{ticket_id}/resolve", headers=admin, json={"note": "called customer"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"

    # terminal
    again = await client.post(f"{INBOX}/{ticket_id}/dismiss", headers=admin, json={"note": "dup"})
    assert again.status_code == 409


async def test_resolve_with_refund_requires_captured_payment(client, db_session):
    """Refund path runs the REAL refund rules — unpaid order → 409, ticket
    stays open."""
    customer = await _customer(client)
    order = await _place_order(client, customer)
    _use(SupportTurn(reply="Team will check.", action="refund_request", order_id=order["id"]))
    ticket_id = (await client.post(CHAT, headers=customer, json={"message": "refund pls"})).json()[
        "escalation_id"
    ]

    admin = await _admin(db_session)
    resp = await client.post(
        f"{INBOX}/{ticket_id}/resolve",
        headers=admin,
        json={"note": "approved refund", "refund": True},
    )
    assert resp.status_code == 409  # PLACED + no captured payment
    ticket = await db_session.get(Escalation, ticket_id)
    await db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.OPEN


async def test_escalations_query_scoped(db_session, client):
    """Escalation listing never mixes users' content into the customer path
    (there is no customer listing endpoint — inbox is admin-only)."""
    paths = set(app.openapi()["paths"])
    assert "/api/v1/support/chat" in paths
    assert not any(p.startswith("/api/v1/support/escalations") for p in paths)
    rows = (await db_session.scalars(select(Escalation))).all()
    assert rows == []
