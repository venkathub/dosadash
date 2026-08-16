"""Addresses + preferences API tests."""

from dosadash_api.db.models import Settings


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    body = (await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


async def test_first_address_becomes_default(client):
    h = await _login(client, "9777777701")
    a1 = await client.post(
        "/api/v1/addresses",
        headers=h,
        json={"label": "Home", "line1": "12 Gandhi St, T Nagar", "pincode": "600017"},
    )
    assert a1.status_code == 201
    assert a1.json()["is_default"] is True

    a2 = await client.post(
        "/api/v1/addresses",
        headers=h,
        json={"label": "Office", "line1": "4 OMR Tech Park", "pincode": "600096"},
    )
    assert a2.json()["is_default"] is False

    # switching default flips the other
    patched = await client.patch(
        f"/api/v1/addresses/{a2.json()['id']}", headers=h, json={"is_default": True}
    )
    assert patched.json()["is_default"] is True
    listing = (await client.get("/api/v1/addresses", headers=h)).json()
    defaults = [a for a in listing if a["is_default"]]
    assert len(defaults) == 1 and defaults[0]["label"] == "Office"


async def test_pincode_serviceability(client, db_session):
    db_session.add(Settings(id=1, delivery_pincodes=["600001", "600017"]))
    await db_session.commit()
    h = await _login(client, "9777777702")
    bad = await client.post(
        "/api/v1/addresses",
        headers=h,
        json={"label": "Far", "line1": "1 Anna Salai", "pincode": "110001"},
    )
    assert bad.status_code == 422
    assert "110001" in bad.json()["detail"]
    ok = await client.post(
        "/api/v1/addresses",
        headers=h,
        json={"label": "Home", "line1": "1 Anna Salai", "pincode": "600001"},
    )
    assert ok.status_code == 201


async def test_address_isolation_and_delete(client):
    h1 = await _login(client, "9777777703")
    h2 = await _login(client, "9777777704")
    addr = (
        await client.post(
            "/api/v1/addresses",
            headers=h1,
            json={"label": "Home", "line1": "7 Usman Rd", "pincode": "600017"},
        )
    ).json()
    # stranger cannot patch or delete
    assert (
        await client.patch(f"/api/v1/addresses/{addr['id']}", headers=h2, json={"label": "Hacked"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/addresses/{addr['id']}", headers=h2)).status_code == 404
    # owner can delete
    assert (await client.delete(f"/api/v1/addresses/{addr['id']}", headers=h1)).status_code == 204
    assert (await client.get("/api/v1/addresses", headers=h1)).json() == []


async def test_checkout_rejects_foreign_address(client):
    h1 = await _login(client, "9777777705")
    h2 = await _login(client, "9777777706")
    addr = (
        await client.post(
            "/api/v1/addresses",
            headers=h1,
            json={"label": "Home", "line1": "9 Mount Rd", "pincode": "600002"},
        )
    ).json()
    menu = (await client.get("/api/v1/menu")).json()
    resp = await client.post(
        "/api/v1/orders",
        headers=h2,
        json={"items": [{"item_id": menu[0]["id"], "qty": 1}], "address_id": addr["id"]},
    )
    assert resp.status_code == 403


async def test_preferences_roundtrip(client):
    h = await _login(client, "9777777707")
    empty = await client.get("/api/v1/preferences", headers=h)
    assert empty.status_code == 200
    assert empty.json() == {"diet": None, "allergens": [], "spice_level": None, "language": "en"}

    put = await client.put(
        "/api/v1/preferences",
        headers=h,
        json={
            "diet": "VEG",
            "allergens": [" Peanut", "cashew "],
            "spice_level": 2,
            "language": "ta",
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["diet"] == "VEG"
    assert body["allergens"] == ["peanut", "cashew"]  # normalized
    assert body["spice_level"] == 2

    again = (await client.get("/api/v1/preferences", headers=h)).json()
    assert again == body


async def test_preferences_validation(client):
    h = await _login(client, "9777777708")
    bad_spice = await client.put("/api/v1/preferences", headers=h, json={"spice_level": 9})
    assert bad_spice.status_code == 422
    bad_diet = await client.put("/api/v1/preferences", headers=h, json={"diet": "CARNIVORE"})
    assert bad_diet.status_code == 422
