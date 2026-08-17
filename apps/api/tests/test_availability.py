"""Business-hours + item-schedule enforcement (IST, frozen-clock tests)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.services import availability
from dosadash_shared import Role

IST = ZoneInfo("Asia/Kolkata")
SAT_LUNCH = datetime(2026, 8, 22, 13, 0, tzinfo=IST)  # saturday 13:00
SAT_NIGHT = datetime(2026, 8, 22, 23, 30, tzinfo=IST)  # saturday 23:30
SUN_LUNCH = datetime(2026, 8, 23, 13, 0, tzinfo=IST)  # sunday 13:00

HOURS = {"sat": {"start": "08:00", "end": "22:00"}}


# ------------------------------------------------------------------ unit level


def test_no_config_means_always_open():
    assert availability.is_open(None)
    assert availability.is_open({})
    assert availability.item_on_schedule(None)


def test_day_window_logic():
    assert availability.is_open(HOURS, SAT_LUNCH)
    assert not availability.is_open(HOURS, SAT_NIGHT)  # after close
    assert not availability.is_open(HOURS, SUN_LUNCH)  # day not configured


def test_overnight_window_spans_midnight():
    late = {"sat": {"start": "18:00", "end": "02:00"}}
    assert availability.item_on_schedule(late, SAT_NIGHT)
    assert not availability.item_on_schedule(late, SAT_LUNCH)


# ----------------------------------------------------------- integration level


async def _admin(db_session) -> dict:
    user = User(phone="+919555559001", name="admin", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _customer(client) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9888888951"})
    otp = req.json()["demo_otp"]
    body = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9888888951", "otp": otp})
    ).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def frozen_saturday_lunch(monkeypatch):
    monkeypatch.setattr(availability, "now_ist", lambda: SAT_LUNCH)


async def test_checkout_blocked_outside_business_hours(client, db_session, monkeypatch):
    admin = await _admin(db_session)
    customer = await _customer(client)
    await client.put(
        "/api/v1/admin/settings",
        headers=admin,
        json={"business_hours": {"sat": {"start": "08:00", "end": "22:00"}}},
    )
    menu = (await client.get("/api/v1/menu")).json()
    order_body = {"items": [{"item_id": menu[0]["id"], "qty": 1}]}

    monkeypatch.setattr(availability, "now_ist", lambda: SAT_NIGHT)
    resp = await client.post("/api/v1/orders", headers=customer, json=order_body)
    assert resp.status_code == 503
    assert "closed" in resp.json()["detail"]

    monkeypatch.setattr(availability, "now_ist", lambda: SAT_LUNCH)
    resp = await client.post("/api/v1/orders", headers=customer, json=order_body)
    assert resp.status_code == 201


async def test_scheduled_item_hidden_and_unorderable_off_window(
    client, db_session, frozen_saturday_lunch, monkeypatch
):
    admin = await _admin(db_session)
    customer = await _customer(client)
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    biryani_id = menu["Chicken Biryani"]["id"]

    # biryani only served weekend evenings
    await client.put(
        f"/api/v1/admin/menu/items/{biryani_id}/schedule",
        headers=admin,
        json={"schedule": {"sat": {"start": "18:00", "end": "23:00"}}},
    )

    names = [i["name"] for i in (await client.get("/api/v1/menu")).json()]
    assert "Chicken Biryani" not in names  # 13:00 — off window
    resp = await client.post(
        "/api/v1/orders", headers=customer, json={"items": [{"item_id": biryani_id, "qty": 1}]}
    )
    assert resp.status_code == 409
    assert "Chicken Biryani" in resp.json()["detail"]

    # 20:00 the same evening — visible and orderable
    evening = datetime(2026, 8, 22, 20, 0, tzinfo=IST)
    monkeypatch.setattr(availability, "now_ist", lambda: evening)
    names = [i["name"] for i in (await client.get("/api/v1/menu")).json()]
    assert "Chicken Biryani" in names
    resp = await client.post(
        "/api/v1/orders", headers=customer, json={"items": [{"item_id": biryani_id, "qty": 1}]}
    )
    assert resp.status_code == 201
