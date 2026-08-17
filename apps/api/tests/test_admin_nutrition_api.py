"""Admin nutrition enrichment tests — AI service mocked via dependency
override (the api never needs a live LLM in tests)."""

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import StaffAction, User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import (
    NutritionEstimate,
    NutritionEstimateResponse,
    Role,
)

NUTRITION = "/api/v1/admin/nutrition"

FAKE_ESTIMATE = NutritionEstimate(
    calories_kcal=340,
    protein_g=7.5,
    carbs_g=55,
    fat_g=10,
    fiber_g=3.5,
    confidence=0.82,
    notes="one dosa with potato masala",
)


class FakeAIClient:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.requests = []
        self.fail_for = fail_for or set()

    async def estimate_nutrition(self, request):
        self.requests.append(request)
        if request.item_name in self.fail_for:
            raise AIServiceError("AI service call failed: boom")
        return NutritionEstimateResponse(
            estimate=FAKE_ESTIMATE, model="gpt-4o-mini", prompt_version="nutrition_v1"
        )


@pytest.fixture
def fake_ai(client):
    from dosadash_api.main import app

    fake = FakeAIClient()
    app.dependency_overrides[get_ai_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_ai_client, None)


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


async def _menu(client) -> dict[str, dict]:
    return {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}


async def test_nutrition_rbac(client, db_session):
    assert (await client.get(NUTRITION)).status_code == 401
    kitchen = await _login_as(db_session, "+919555558002", Role.KITCHEN_STAFF)
    assert (await client.get(NUTRITION, headers=kitchen)).status_code == 403


async def test_enrich_drafts_with_recipe_context(client, admin, fake_ai, db_session):
    menu = await _menu(client)
    dosa_id = menu["Masala Dosa"]["id"]
    resp = await client.post(
        f"{NUTRITION}/enrich", headers=admin, json={"item_ids": [dosa_id, 999999]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["enriched"]) == 1
    draft = body["enriched"][0]
    assert draft["status"] == "DRAFT"
    assert draft["model"] == "gpt-4o-mini"
    assert draft["prompt_version"] == "nutrition_v1"
    assert draft["estimate"]["calories_kcal"] == 340
    assert body["failed"] == [{"item_id": 999999, "error": "unknown item id"}]

    # AI received the recipe mapping as context
    assert fake_ai.requests[0].item_name == "Masala Dosa"
    assert [line.name for line in fake_ai.requests[0].recipe] == ["idli rice"]

    # audited
    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "nutrition.enrich")
    )
    assert action.detail["item_ids"] == [dosa_id]

    # draft is NOT public
    detail = (await client.get(f"/api/v1/menu/items/{dosa_id}")).json()
    assert detail["nutrition"] is None


async def test_enrich_requires_recipe_mapping(client, admin, fake_ai, db_session):
    # create a recipe-less item, then try to enrich it
    created = await client.post(
        "/api/v1/admin/menu/items",
        headers=admin,
        json={"name": "Plain Salt Sundal", "category": "Snacks", "price": "50"},
    )
    item_id = created.json()["id"]
    resp = await client.post(f"{NUTRITION}/enrich", headers=admin, json={"item_ids": [item_id]})
    assert resp.json()["failed"][0]["error"].startswith("no recipe mapping")
    assert fake_ai.requests == []


async def test_enrich_survives_partial_ai_failure(client, admin, db_session):
    from dosadash_api.main import app

    fake = FakeAIClient(fail_for={"Masala Dosa"})
    app.dependency_overrides[get_ai_client] = lambda: fake
    try:
        menu = await _menu(client)
        resp = await client.post(
            f"{NUTRITION}/enrich",
            headers=admin,
            json={"item_ids": [menu["Masala Dosa"]["id"], menu["Filter Coffee"]["id"]]},
        )
        body = resp.json()
        assert [f["item_id"] for f in body["failed"]] == [menu["Masala Dosa"]["id"]]
        assert [e["item_id"] for e in body["enriched"]] == [menu["Filter Coffee"]["id"]]
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_approval_gate_controls_public_visibility(client, admin, fake_ai):
    menu = await _menu(client)
    dosa_id = menu["Masala Dosa"]["id"]
    await client.post(f"{NUTRITION}/enrich", headers=admin, json={"item_ids": [dosa_id]})

    # approve → public
    approved = await client.post(
        f"{NUTRITION}/{dosa_id}/status", headers=admin, json={"status": "APPROVED"}
    )
    assert approved.status_code == 200
    assert approved.json()["reviewed_by"] is not None
    detail = (await client.get(f"/api/v1/menu/items/{dosa_id}")).json()
    assert detail["nutrition"]["calories_kcal"] == 340

    # reject → gone from public again
    await client.post(f"{NUTRITION}/{dosa_id}/status", headers=admin, json={"status": "REJECTED"})
    detail = (await client.get(f"/api/v1/menu/items/{dosa_id}")).json()
    assert detail["nutrition"] is None

    # same-status transition refused; unknown item 404
    resp = await client.post(
        f"{NUTRITION}/{dosa_id}/status", headers=admin, json={"status": "REJECTED"}
    )
    assert resp.status_code == 409
    resp = await client.post(
        f"{NUTRITION}/999999/status", headers=admin, json={"status": "APPROVED"}
    )
    assert resp.status_code == 404


async def test_re_enrich_resets_to_draft(client, admin, fake_ai):
    menu = await _menu(client)
    dosa_id = menu["Masala Dosa"]["id"]
    await client.post(f"{NUTRITION}/enrich", headers=admin, json={"item_ids": [dosa_id]})
    await client.post(f"{NUTRITION}/{dosa_id}/status", headers=admin, json={"status": "APPROVED"})

    # recipe changed → re-enrich → must demand fresh review
    await client.post(f"{NUTRITION}/enrich", headers=admin, json={"item_ids": [dosa_id]})
    listing = (await client.get(NUTRITION, headers=admin, params={"status": "DRAFT"})).json()
    assert [n["item_id"] for n in listing] == [dosa_id]
    assert listing[0]["reviewed_by"] is None

    detail = (await client.get(f"/api/v1/menu/items/{dosa_id}")).json()
    assert detail["nutrition"] is None  # approval did not survive re-enrichment
