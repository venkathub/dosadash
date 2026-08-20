"""Admin menu-translation tests — AI service mocked via dependency override
(the api never needs a live LLM in tests)."""

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import MenuItem, MenuItemTranslation, StaffAction, User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import (
    MenuTranslationResponse,
    Role,
    TranslationDraft,
    TranslationRejection,
)

TRANSLATIONS = "/api/v1/admin/translations"


class FakeAIClient:
    """Echoes a Tamil draft for every requested item (minus configured
    failures/rejections), like the real ai-side sanitizer would."""

    def __init__(self, fail: bool = False, reject_names: set[str] | None = None) -> None:
        self.requests = []
        self.fail = fail
        self.reject_names = reject_names or set()

    async def translate_menu(self, request) -> MenuTranslationResponse:
        self.requests.append(request)
        if self.fail:
            raise AIServiceError("AI service call failed: boom")
        translations, rejected = [], []
        for item in request.items:
            if item.name in self.reject_names:
                rejected.append(
                    TranslationRejection(item_id=item.item_id, reason="missing from model output")
                )
            else:
                translations.append(
                    TranslationDraft(
                        item_id=item.item_id,
                        name=f"தமிழ் {item.name}",
                        description="தமிழ் விளக்கம்",
                        category_label="டிஃபின்",
                    )
                )
        return MenuTranslationResponse(
            translations=translations, rejected=rejected, model="gpt-4o-mini"
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
    return await _login_as(db_session, "+919555559001", Role.ADMIN)


async def _item_ids(db_session) -> dict[str, int]:
    rows = (await db_session.scalars(select(MenuItem))).all()
    return {m.name: m.id for m in rows}


async def test_translations_rbac(client, db_session):
    assert (await client.get(TRANSLATIONS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555559002", Role.KITCHEN_STAFF)
    assert (await client.get(TRANSLATIONS, headers=kitchen)).status_code == 403


async def test_unsupported_language_is_422(client, admin, fake_ai):
    resp = await client.post(f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "fr"})
    assert resp.status_code == 422
    assert fake_ai.requests == []


async def test_draft_all_fills_gaps_only(client, admin, fake_ai, db_session):
    items = await _item_ids(db_session)
    resp = await client.post(f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["drafted"]) == len(items)  # every seeded item, incl. 86'd ones
    draft = body["drafted"][0]
    assert draft["status"] == "DRAFT"
    assert draft["lang"] == "ta"
    assert draft["name"].startswith("தமிழ் ")
    assert draft["model"] == "gpt-4o-mini"
    assert draft["prompt_version"] == "menu_translation_v1"

    # audited
    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "translation.draft")
    )
    assert action.detail["lang"] == "ta"
    assert sorted(action.detail["item_ids"]) == sorted(items.values())

    # draft-all again: everything already has a row → nothing re-drafted
    resp = await client.post(f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta"})
    assert resp.json() == {"drafted": [], "failed": []}
    assert len(fake_ai.requests) == 1  # second call never reached the AI


async def test_draft_explicit_ids_reports_unknown_and_rejected(client, admin, db_session):
    from dosadash_api.main import app

    fake = FakeAIClient(reject_names={"Masala Dosa"})
    app.dependency_overrides[get_ai_client] = lambda: fake
    try:
        items = await _item_ids(db_session)
        resp = await client.post(
            f"{TRANSLATIONS}/draft",
            headers=admin,
            json={"lang": "ta", "item_ids": [items["Masala Dosa"], items["Filter Coffee"], 999999]},
        )
        body = resp.json()
        assert [d["item_id"] for d in body["drafted"]] == [items["Filter Coffee"]]
        errors = {f["item_id"]: f["error"] for f in body["failed"]}
        assert errors[999999] == "unknown item id"
        assert "missing" in errors[items["Masala Dosa"]]
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_draft_survives_ai_outage(client, admin, db_session):
    from dosadash_api.main import app

    fake = FakeAIClient(fail=True)
    app.dependency_overrides[get_ai_client] = lambda: fake
    try:
        items = await _item_ids(db_session)
        resp = await client.post(
            f"{TRANSLATIONS}/draft",
            headers=admin,
            json={"lang": "ta", "item_ids": [items["Masala Dosa"]]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["drafted"] == []
        assert "AI service call failed" in body["failed"][0]["error"]
    finally:
        app.dependency_overrides.pop(get_ai_client, None)


async def test_approval_gate_and_status_transitions(client, admin, fake_ai, db_session):
    items = await _item_ids(db_session)
    dosa = items["Masala Dosa"]
    await client.post(
        f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta", "item_ids": [dosa]}
    )

    approved = await client.post(
        f"{TRANSLATIONS}/{dosa}/ta/status", headers=admin, json={"status": "APPROVED"}
    )
    assert approved.status_code == 200
    assert approved.json()["reviewed_by"] is not None

    # same-status transition refused; unknown row 404; bad lang 422
    resp = await client.post(
        f"{TRANSLATIONS}/{dosa}/ta/status", headers=admin, json={"status": "APPROVED"}
    )
    assert resp.status_code == 409
    resp = await client.post(
        f"{TRANSLATIONS}/999999/ta/status", headers=admin, json={"status": "APPROVED"}
    )
    assert resp.status_code == 404
    resp = await client.post(
        f"{TRANSLATIONS}/{dosa}/fr/status", headers=admin, json={"status": "APPROVED"}
    )
    assert resp.status_code == 422

    # audited with from/to
    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "translation.status")
    )
    assert action.detail == {"lang": "ta", "from": "DRAFT", "to": "APPROVED"}


async def test_edit_resets_to_draft(client, admin, fake_ai, db_session):
    items = await _item_ids(db_session)
    dosa = items["Masala Dosa"]
    await client.post(
        f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta", "item_ids": [dosa]}
    )
    await client.post(
        f"{TRANSLATIONS}/{dosa}/ta/status", headers=admin, json={"status": "APPROVED"}
    )

    resp = await client.patch(
        f"{TRANSLATIONS}/{dosa}/ta",
        headers=admin,
        json={"name": "மசாலா தோசை", "description": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "மசாலா தோசை"
    assert body["description"] is None  # explicit null clears the field
    assert body["status"] == "DRAFT"  # approval never survives an edit
    assert body["reviewed_by"] is None

    # empty edit refused
    resp = await client.patch(f"{TRANSLATIONS}/{dosa}/ta", headers=admin, json={})
    assert resp.status_code == 422


async def test_re_draft_resets_to_draft(client, admin, fake_ai, db_session):
    items = await _item_ids(db_session)
    coffee = items["Filter Coffee"]
    await client.post(
        f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta", "item_ids": [coffee]}
    )
    await client.post(
        f"{TRANSLATIONS}/{coffee}/ta/status", headers=admin, json={"status": "APPROVED"}
    )

    # explicit re-draft is the deliberate way to overwrite a reviewed row
    await client.post(
        f"{TRANSLATIONS}/draft", headers=admin, json={"lang": "ta", "item_ids": [coffee]}
    )
    listing = (
        await client.get(TRANSLATIONS, headers=admin, params={"lang": "ta", "status": "DRAFT"})
    ).json()
    assert [t["item_id"] for t in listing] == [coffee]
    assert listing[0]["reviewed_by"] is None

    record = await db_session.get(MenuItemTranslation, (coffee, "ta"))
    assert record.status == "DRAFT"
