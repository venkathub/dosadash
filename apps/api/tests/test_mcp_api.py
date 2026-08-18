"""Phase 6 MCP: internal endpoints + adapter tool behavior."""

from sqlalchemy import select, text

from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.routers.internal_mcp import MCP_DEMO_PHONE
from dosadash_shared import Role

INVENTORY = "/api/v1/internal/mcp/inventory"
PLACE = "/api/v1/internal/mcp/place"


def _internal(monkeypatch) -> dict:
    monkeypatch.setenv("API_INTERNAL_API_TOKEN", "test-internal")
    get_settings.cache_clear()
    return {"X-Internal-Token": "test-internal"}


async def _menu(client) -> dict[str, dict]:
    return {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}


async def test_internal_token_required(client, db_session, monkeypatch):
    _internal(monkeypatch)
    assert (await client.get(INVENTORY)).status_code == 403
    assert (await client.get(INVENTORY, headers={"X-Internal-Token": "nope"})).status_code == 403
    assert (await client.post(PLACE, json={"items": [{"item_id": 1, "qty": 1}]})).status_code == 403


async def test_inventory_snapshot_with_low_flags(client, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    resp = await client.get(INVENTORY, headers=headers)
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()}
    assert "idli rice" in rows
    # conftest seeds stock 0 / reorder 0 → at-or-below reorder point = low
    assert rows["idli rice"]["low"] is True
    assert set(rows["idli rice"]) == {
        "ingredient_id",
        "name",
        "unit",
        "stock_qty",
        "reorder_point",
        "low",
    }


async def test_place_creates_real_order_for_demo_user(client, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    menu = await _menu(client)
    resp = await client.post(
        PLACE,
        headers=headers,
        json={
            "items": [
                {"item_id": menu["Masala Dosa"]["id"], "qty": 1},
                {"item_id": menu["Filter Coffee"]["id"], "qty": 1},
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    order = resp.json()
    assert order["status"] == "PLACED"
    assert order["total"] == "189.00"  # (120+60) × 1.05 GST

    demo_user = await db_session.scalar(select(User).where(User.phone == MCP_DEMO_PHONE))
    assert demo_user is not None
    assert demo_user.role == Role.CUSTOMER
    assert demo_user.name == "Claude (MCP demo)"

    # idempotent identity: second order reuses the same demo user
    again = await client.post(
        PLACE, headers=headers, json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]}
    )
    assert again.status_code == 201


async def test_place_rejects_hallucinated_items(client, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    resp = await client.post(
        PLACE, headers=headers, json={"items": [{"item_id": 999999, "qty": 1}]}
    )
    assert resp.status_code == 404  # Hard Rule 2: unknown item_id never ordered


async def test_place_respects_sold_out_and_pause(client, db_session, monkeypatch):
    headers = _internal(monkeypatch)
    menu = await _menu(client)

    off = await db_session.execute(
        text("SELECT id FROM menu_items WHERE is_available = false LIMIT 1")
    )
    off_id = off.scalar()
    sold_out = await client.post(
        PLACE, headers=headers, json={"items": [{"item_id": off_id, "qty": 1}]}
    )
    assert sold_out.status_code == 409

    await db_session.execute(
        text(
            "INSERT INTO settings (id, kitchen_paused, delivery_pincodes) "
            "VALUES (1, true, '{}') "
            "ON CONFLICT (id) DO UPDATE SET kitchen_paused = true"
        )
    )
    await db_session.commit()
    paused = await client.post(
        PLACE, headers=headers, json={"items": [{"item_id": menu["Masala Dosa"]["id"], "qty": 1}]}
    )
    assert paused.status_code == 503


async def test_mcp_tools_shape():
    """The adapter's tool surface: three tools, correct names, thin HTTP."""
    from dosadash_ai import mcp_server

    tools = await mcp_server.server.list_tools()
    names = {t.name for t in tools}
    assert names == {"get_menu", "check_inventory", "place_order"}
    place = next(t for t in tools if t.name == "place_order")
    assert "REAL" in (place.description or "")
