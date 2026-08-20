"""Phase 8 customer review endpoints: DELIVERED-only, one per order, owned."""

from datetime import UTC, datetime

from sqlalchemy import select

from dosadash_api.db.models import Order, Review
from dosadash_shared import OrderState

REVIEW = "/api/v1/orders/{order_id}/review"


async def _customer(client, phone="9111177001") -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _place_order(client, customer) -> int:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        headers=customer,
        json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _deliver(db_session, order_id: int) -> None:
    order = await db_session.scalar(select(Order).where(Order.id == order_id))
    order.status = OrderState.DELIVERED
    order.delivered_at = datetime.now(UTC)
    await db_session.commit()


# ------------------------------------------------------------------ create


async def test_review_delivered_order(client, db_session):
    customer = await _customer(client)
    order_id = await _place_order(client, customer)
    await _deliver(db_session, order_id)

    resp = await client.post(
        REVIEW.format(order_id=order_id),
        headers=customer,
        json={"rating": 4, "text": "Loved the dosa, delivery was a bit slow."},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["rating"] == 4
    assert body["order_id"] == order_id
    # scoring internals never leak to the customer wire shape
    assert "sentiment" not in body
    assert "aspects" not in body
    assert "reply_draft" not in body

    row = await db_session.scalar(select(Review).where(Review.order_id == order_id))
    assert row.sentiment is None  # unscored until the scoring path runs


async def test_review_requires_delivered_status(client, db_session):
    customer = await _customer(client, "9111177002")
    order_id = await _place_order(client, customer)  # PLACED, not delivered
    resp = await client.post(REVIEW.format(order_id=order_id), headers=customer, json={"rating": 5})
    assert resp.status_code == 409


async def test_one_review_per_order(client, db_session):
    customer = await _customer(client, "9111177003")
    order_id = await _place_order(client, customer)
    await _deliver(db_session, order_id)
    first = await client.post(
        REVIEW.format(order_id=order_id), headers=customer, json={"rating": 5, "text": "Super!"}
    )
    assert first.status_code == 201
    second = await client.post(
        REVIEW.format(order_id=order_id), headers=customer, json={"rating": 1, "text": "Changed."}
    )
    assert second.status_code == 409


async def test_foreign_order_is_404_not_403(client, db_session):
    owner = await _customer(client, "9111177004")
    order_id = await _place_order(client, owner)
    await _deliver(db_session, order_id)
    stranger = await _customer(client, "9111177005")
    resp = await client.post(
        REVIEW.format(order_id=order_id), headers=stranger, json={"rating": 1, "text": "hah"}
    )
    assert resp.status_code == 404  # existence not leaked


async def test_rating_bounds_enforced(client, db_session):
    customer = await _customer(client, "9111177006")
    order_id = await _place_order(client, customer)
    await _deliver(db_session, order_id)
    for bad in (0, 6):
        resp = await client.post(
            REVIEW.format(order_id=order_id), headers=customer, json={"rating": bad}
        )
        assert resp.status_code == 422, bad


async def test_rating_only_review_allowed(client, db_session):
    customer = await _customer(client, "9111177007")
    order_id = await _place_order(client, customer)
    await _deliver(db_session, order_id)
    resp = await client.post(REVIEW.format(order_id=order_id), headers=customer, json={"rating": 5})
    assert resp.status_code == 201
    assert resp.json()["text"] == ""


# ------------------------------------------------------------------ read


async def test_get_own_review(client, db_session):
    customer = await _customer(client, "9111177008")
    order_id = await _place_order(client, customer)
    await _deliver(db_session, order_id)
    await client.post(
        REVIEW.format(order_id=order_id), headers=customer, json={"rating": 3, "text": "Okay."}
    )
    resp = await client.get(REVIEW.format(order_id=order_id), headers=customer)
    assert resp.status_code == 200
    assert resp.json()["rating"] == 3


async def test_get_review_missing_is_404(client, db_session):
    customer = await _customer(client, "9111177009")
    order_id = await _place_order(client, customer)
    resp = await client.get(REVIEW.format(order_id=order_id), headers=customer)
    assert resp.status_code == 404


async def test_review_requires_auth(client, db_session):
    resp = await client.post(REVIEW.format(order_id=1), json={"rating": 5})
    assert resp.status_code in (401, 403)
