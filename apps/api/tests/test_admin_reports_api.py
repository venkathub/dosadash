"""Admin reports: RBAC, sales rollups, dish P&L, GST CSV, forecast-vs-actual."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Forecast, MenuItem, Order, OrderItem, User
from dosadash_shared import ChannelType, OrderState, Role


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
    return await _login_as(db_session, "+919555558001", Role.ADMIN)


@pytest.fixture
async def customer(db_session):
    return await _login_as(db_session, "+919555558002", Role.CUSTOMER)


@pytest.fixture
async def seeded_orders(db_session):
    """Two delivered orders yesterday (IST-safe midday), one cancelled."""
    items = (await db_session.scalars(select(MenuItem))).all()
    masala = next(i for i in items if i.name == "Masala Dosa")  # ₹120
    coffee = next(i for i in items if i.name == "Filter Coffee")  # ₹60
    user = User(phone="+919555558099", name="Buyer", role=Role.CUSTOMER)
    db_session.add(user)
    await db_session.flush()

    yesterday_noon = datetime.now(UTC).replace(
        hour=7, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)  # 12:30 IST

    def order(status: OrderState, subtotal: str, items_):
        o = Order(
            user_id=user.id,
            brand_id=masala.brand_id,
            channel=ChannelType.WEB,
            status=status,
            subtotal=Decimal(subtotal),
            gst=Decimal(subtotal) * Decimal("0.05"),
            total=Decimal(subtotal) * Decimal("1.05"),
            placed_at=yesterday_noon,
        )
        o.items = items_
        return o

    db_session.add_all(
        [
            order(
                OrderState.DELIVERED,
                "240",
                [OrderItem(item_id=masala.id, qty=2, unit_price=masala.price)],
            ),
            order(
                OrderState.DELIVERED,
                "60",
                [OrderItem(item_id=coffee.id, qty=1, unit_price=coffee.price)],
            ),
            order(
                OrderState.CANCELLED,
                "120",
                [OrderItem(item_id=masala.id, qty=1, unit_price=masala.price)],
            ),
        ]
    )
    await db_session.commit()
    return {"masala": masala, "coffee": coffee, "day": yesterday_noon}


async def test_rbac_admin_only(client, customer):
    for path in (
        "/api/v1/admin/reports/sales",
        "/api/v1/admin/reports/dish-pnl",
        "/api/v1/admin/reports/gst.csv",
        "/api/v1/admin/reports/forecast-vs-actual",
        "/api/v1/admin/crm/segments",
    ):
        assert (await client.get(path, headers=customer)).status_code == 403


async def test_sales_excludes_cancelled(client, admin, seeded_orders):
    resp = await client.get("/api/v1/admin/reports/sales?days=7", headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_orders"] == 2
    assert body["total_revenue"] == pytest.approx(315.0)  # (240+60)*1.05
    assert body["total_gst"] == pytest.approx(15.0)
    assert len(body["buckets"]) == 1
    assert body["buckets"][0]["aov"] == pytest.approx(157.5)


async def test_dish_pnl_estimated_costs(client, admin, seeded_orders):
    resp = await client.get("/api/v1/admin/reports/dish-pnl?days=7", headers=admin)
    assert resp.status_code == 200
    rows = {r["name"]: r for r in resp.json()["rows"]}
    masala = rows["Masala Dosa"]
    assert masala["qty"] == 2 and masala["revenue"] == pytest.approx(240.0)
    # No ingredient costs in the fixture → labeled 35% estimate
    assert masala["cost_source"] == "estimated"
    assert masala["ingredient_cost"] == pytest.approx(0.35 * 120 * 2)
    assert masala["margin_pct"] == pytest.approx(65.0)
    assert "Seasonal Special" not in rows  # never ordered


async def test_gst_csv_download(client, admin, seeded_orders):
    month = seeded_orders["day"].astimezone().strftime("%Y-%m")
    resp = await client.get(f"/api/v1/admin/reports/gst.csv?month={month}", headers=admin)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = [ln for ln in resp.text.strip().splitlines() if ln]
    assert lines[0].startswith("order_id,date,channel,taxable_value_inr")
    assert lines[-1].startswith("TOTALS")
    assert "2 orders" in lines[-1]  # cancelled excluded


async def test_forecast_vs_actual_flags_anomaly(client, admin, seeded_orders, db_session):
    masala = seeded_orders["masala"]
    # Anchor on the fixture's seeded IST day: deriving "yesterday" from the
    # wall clock flaked between 18:30 and 00:00 UTC (IST is already tomorrow).
    yesterday = seeded_orders["day"].astimezone(ZoneInfo("Asia/Kolkata")).date()
    # Forecast said 40 masala dosas; actual was 2 → day + dish anomaly.
    db_session.add(
        Forecast(item_id=masala.id, date=yesterday, predicted_qty=40.0, model_version="test/v1")
    )
    db_session.add(
        Forecast(
            item_id=masala.id,
            date=yesterday + timedelta(days=2),
            predicted_qty=12.0,
            model_version="test/v1",
        )
    )
    await db_session.commit()

    resp = await client.get("/api/v1/admin/reports/forecast-vs-actual?days=7", headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_version"] == "test/v1"
    by_date = {p["date"]: p for p in body["points"]}
    flagged = by_date[yesterday.isoformat()]
    assert flagged["anomaly"] is True
    assert flagged["forecast_qty"] == 40.0 and flagged["actual_qty"] == 3.0
    future = by_date[(yesterday + timedelta(days=2)).isoformat()]
    assert future["actual_qty"] is None and future["anomaly"] is False
    assert any(a["name"] == "Masala Dosa" for a in body["dish_anomalies"])
