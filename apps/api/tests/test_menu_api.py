async def test_list_menu_hides_unavailable(client):
    resp = await client.get("/api/v1/menu")
    assert resp.status_code == 200
    names = [i["name"] for i in resp.json()]
    assert "Masala Dosa" in names
    assert "Seasonal Special" not in names  # 86'd item hidden
    assert len(names) == 4


async def test_list_menu_veg_filter(client):
    resp = await client.get("/api/v1/menu", params={"veg": "true"})
    names = [i["name"] for i in resp.json()]
    assert "Chicken Biryani" not in names
    assert "Masala Dosa" in names


async def test_list_menu_category_case_insensitive(client):
    resp = await client.get("/api/v1/menu", params={"category": "dosa"})
    assert [i["name"] for i in resp.json()] == ["Masala Dosa"]


async def test_list_menu_excludes_allergens(client):
    resp = await client.get("/api/v1/menu", params={"exclude_allergens": ["peanut", "milk"]})
    names = [i["name"] for i in resp.json()]
    assert "Lemon Rice" not in names  # peanut
    assert "Filter Coffee" not in names  # milk
    assert "Masala Dosa" in names


async def test_list_menu_search(client):
    resp = await client.get("/api/v1/menu", params={"q": "biry"})
    assert [i["name"] for i in resp.json()] == ["Chicken Biryani"]


async def test_summary_includes_allergen_badges(client):
    resp = await client.get("/api/v1/menu", params={"q": "lemon"})
    (lemon,) = resp.json()
    assert lemon["allergens"] == ["peanut"]


async def test_item_detail(client):
    listing = await client.get("/api/v1/menu", params={"q": "masala"})
    item_id = listing.json()[0]["id"]
    resp = await client.get(f"/api/v1/menu/items/{item_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Masala Dosa"
    assert body["ingredients"] == ["idli rice"]
    assert body["gst_rate"] == "5.00"
    assert [c["name"] for c in body["customizations"]] == ["Extra ghee"]


async def test_item_detail_404(client):
    resp = await client.get("/api/v1/menu/items/999999")
    assert resp.status_code == 404


async def test_categories_with_counts(client):
    resp = await client.get("/api/v1/menu/categories")
    cats = {c["name"]: c["item_count"] for c in resp.json()}
    assert cats == {"Beverages": 1, "Biryani": 1, "Dosa": 1, "Rice & Pongal": 1}
