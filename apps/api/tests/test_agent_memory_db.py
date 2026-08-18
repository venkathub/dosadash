"""Phase 6 agent memory: episodic writes + "my usual" derivation (real DB)."""

from decimal import Decimal

from sqlalchemy import select, text

from dosadash_ai.agent.context import (
    USUAL_MIN_REPEATS,
    UserMemoryCtx,
    load_context,
    load_memory,
    memory_payload,
)
from dosadash_api.db.models import UserMemory


async def _customer(client, phone="9111111111") -> tuple[dict, int]:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    body = verify.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def _place(client, headers, items: list[dict]) -> dict:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    resp = await client.post(
        "/api/v1/orders",
        headers=headers,
        json={"items": [{"item_id": menu[i["name"]]["id"], "qty": i["qty"]} for i in items]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_checkout_writes_episode_memory(client, db_session):
    headers, user_id = await _customer(client)
    order = await _place(
        client, headers, [{"name": "Masala Dosa", "qty": 2}, {"name": "Filter Coffee", "qty": 1}]
    )

    memory = await db_session.scalar(select(UserMemory).where(UserMemory.user_id == user_id))
    assert memory is not None
    assert memory.kind == "EPISODE"
    assert "2× Masala Dosa" in memory.content
    assert "1× Filter Coffee" in memory.content
    assert order["total"] in memory.content
    assert {line["qty"] for line in memory.meta["items"]} == {1, 2}


async def test_usual_derived_from_repeated_signature(client, db_session):
    headers, user_id = await _customer(client)
    for _ in range(3):
        await _place(
            client,
            headers,
            [{"name": "Masala Dosa", "qty": 2}, {"name": "Filter Coffee", "qty": 1}],
        )
    await _place(client, headers, [{"name": "Lemon Rice", "qty": 1}])  # one-off, not the usual

    memory = await load_memory(db_session, user_id)
    assert memory.usual is not None
    assert memory.usual["times_ordered"] == 3
    assert {(i["name"], i["qty"]) for i in memory.usual["items"]} == {
        ("Masala Dosa", 2),
        ("Filter Coffee", 1),
    }
    # episodes newest-first, capped
    assert len(memory.recent_orders) == 3
    assert "Lemon Rice" in memory.recent_orders[0]


async def test_no_usual_below_repeat_floor(client, db_session):
    headers, user_id = await _customer(client)
    await _place(client, headers, [{"name": "Masala Dosa", "qty": 1}])
    assert USUAL_MIN_REPEATS >= 2
    memory = await load_memory(db_session, user_id)
    assert memory.usual is None
    assert len(memory.recent_orders) == 1


async def test_cancelled_orders_do_not_form_a_usual(client, db_session):
    headers, user_id = await _customer(client)
    for _ in range(2):
        order = await _place(client, headers, [{"name": "Masala Dosa", "qty": 1}])
        await client.post(f"/api/v1/orders/{order['id']}/cancel", headers=headers)
    memory = await load_memory(db_session, user_id)
    assert memory.usual is None


async def test_context_carries_memory_only_for_logged_in_users(client, db_session):
    headers, user_id = await _customer(client)
    for _ in range(2):
        await _place(client, headers, [{"name": "Filter Coffee", "qty": 1}])

    ctx = await load_context(db_session, user_id)
    assert ctx.memory is not None
    assert ctx.memory.usual["items"][0]["name"] == "Filter Coffee"
    payload = memory_payload(ctx)
    assert payload["usual"]["times_ordered"] == 2

    anon = await load_context(db_session, None)
    assert anon.memory is None
    assert memory_payload(anon) is None


async def test_memory_cascade_deleted_with_user(client, db_session):
    headers, user_id = await _customer(client)
    await _place(client, headers, [{"name": "Masala Dosa", "qty": 1}])
    # FK ondelete CASCADE: wiping the user wipes their memories (PII hygiene)
    await db_session.execute(text("DELETE FROM refresh_tokens WHERE user_id = :u"), {"u": user_id})
    await db_session.execute(
        text("DELETE FROM payments WHERE order_id IN (SELECT id FROM orders WHERE user_id = :u)"),
        {"u": user_id},
    )
    await db_session.execute(
        text(
            "DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE user_id = :u)"
        ),
        {"u": user_id},
    )
    await db_session.execute(text("DELETE FROM orders WHERE user_id = :u"), {"u": user_id})
    await db_session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    await db_session.commit()
    remaining = (
        await db_session.scalars(select(UserMemory).where(UserMemory.user_id == user_id))
    ).all()
    assert remaining == []


def test_memory_ctx_defaults():
    ctx = UserMemoryCtx()
    assert ctx.usual is None and ctx.recent_orders == ()
    assert Decimal("1") == 1  # keep Decimal import honest
