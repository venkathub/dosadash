"""Public menu localization tests (Phase 7): ?lang=ta serves owner-APPROVED
translations with canonical fallback; drafts never serve; canonical text
edits pull translations back to DRAFT."""

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import MenuItem, MenuItemTranslation, StaffAction, User
from dosadash_shared import Role

MENU = "/api/v1/menu"


async def _item_id(db_session, name: str) -> int:
    return await db_session.scalar(select(MenuItem.id).where(MenuItem.name == name))


async def _add_translation(
    db_session,
    item_id: int,
    *,
    status: str = "APPROVED",
    name: str = "மசாலா தோசை",
    description: str | None = "உருளைக்கிழங்கு மசாலாவுடன்",
    category_label: str | None = "தோசை",
) -> MenuItemTranslation:
    row = MenuItemTranslation(
        item_id=item_id,
        lang="ta",
        name=name,
        description=description,
        category_label=category_label,
        status=status,
        model="gpt-4o-mini",
        prompt_version="menu_translation_v1",
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.fixture
async def admin(db_session):
    user = User(phone="+919555559101", name="admin user", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def test_lang_ta_serves_approved_overlay(client, db_session):
    dosa = await _item_id(db_session, "Masala Dosa")
    await _add_translation(db_session, dosa)

    localized = {m["id"]: m for m in (await client.get(MENU, params={"lang": "ta"})).json()}
    row = localized[dosa]
    assert row["name"] == "மசாலா தோசை"
    assert row["canonical_name"] == "Masala Dosa"
    assert row["description"] == "உருளைக்கிழங்கு மசாலாவுடன்"
    assert row["category"] == "Dosa"  # canonical key never localized
    assert row["category_label"] == "தோசை"

    # other items fall back to canonical, untouched
    others = [m for m in localized.values() if m["id"] != dosa]
    assert all(m["canonical_name"] is None for m in others)


async def test_without_lang_menu_is_canonical(client, db_session):
    dosa = await _item_id(db_session, "Masala Dosa")
    await _add_translation(db_session, dosa)
    plain = {m["id"]: m for m in (await client.get(MENU)).json()}
    assert plain[dosa]["name"] == "Masala Dosa"
    assert plain[dosa]["canonical_name"] is None
    assert plain[dosa]["category_label"] is None


async def test_drafts_and_rejected_never_serve(client, db_session):
    dosa = await _item_id(db_session, "Masala Dosa")
    coffee = await _item_id(db_session, "Filter Coffee")
    await _add_translation(db_session, dosa, status="DRAFT")
    await _add_translation(db_session, coffee, status="REJECTED", name="ஃபில்டர் காபி")

    localized = {m["id"]: m for m in (await client.get(MENU, params={"lang": "ta"})).json()}
    assert localized[dosa]["name"] == "Masala Dosa"
    assert localized[coffee]["name"] == "Filter Coffee"


async def test_description_falls_back_per_field(client, db_session):
    dosa = await _item_id(db_session, "Masala Dosa")
    english_desc = (await client.get(f"{MENU}/items/{dosa}")).json()["description"]
    await _add_translation(db_session, dosa, description=None, category_label=None)

    detail = (await client.get(f"{MENU}/items/{dosa}", params={"lang": "ta"})).json()
    assert detail["name"] == "மசாலா தோசை"
    assert detail["description"] == english_desc  # canonical fallback
    assert detail["category_label"] is None


async def test_unsupported_lang_is_422_everywhere(client):
    assert (await client.get(MENU, params={"lang": "fr"})).status_code == 422
    assert (await client.get(f"{MENU}/categories", params={"lang": "fr"})).status_code == 422
    assert (await client.get(f"{MENU}/items/1", params={"lang": "fr"})).status_code == 422


async def test_categories_carry_localized_labels(client, db_session):
    dosa = await _item_id(db_session, "Masala Dosa")
    await _add_translation(db_session, dosa)

    cats = {c["name"]: c for c in (await client.get(f"{MENU}/categories")).json()}
    assert cats["Dosa"]["label"] is None  # no lang requested

    cats = {
        c["name"]: c for c in (await client.get(f"{MENU}/categories", params={"lang": "ta"})).json()
    }
    assert cats["Dosa"]["label"] == "தோசை"
    assert cats["Beverages"]["label"] is None  # nothing approved there yet


async def test_canonical_edit_resets_translation_to_draft(client, db_session, admin):
    """Stale Tamil never serves: renaming the English row pulls its APPROVED
    translation back to DRAFT in the same transaction."""
    dosa = await _item_id(db_session, "Masala Dosa")
    await _add_translation(db_session, dosa)

    resp = await client.patch(
        f"/api/v1/admin/menu/items/{dosa}", headers=admin, json={"name": "Mysore Masala Dosa"}
    )
    assert resp.status_code == 200, resp.text

    row = await db_session.get(MenuItemTranslation, (dosa, "ta"))
    await db_session.refresh(row)
    assert row.status == "DRAFT"
    assert row.reviewed_by is None

    localized = {m["id"]: m for m in (await client.get(MENU, params={"lang": "ta"})).json()}
    assert localized[dosa]["name"] == "Mysore Masala Dosa"  # canonical, not stale Tamil

    action = await db_session.scalar(select(StaffAction).where(StaffAction.action == "menu.update"))
    assert action.detail["translations_reset"] == ["ta"]


async def test_price_edit_does_not_reset_translation(client, db_session, admin):
    dosa = await _item_id(db_session, "Masala Dosa")
    await _add_translation(db_session, dosa)

    resp = await client.patch(
        f"/api/v1/admin/menu/items/{dosa}", headers=admin, json={"price": "135.00"}
    )
    assert resp.status_code == 200

    row = await db_session.get(MenuItemTranslation, (dosa, "ta"))
    await db_session.refresh(row)
    assert row.status == "APPROVED"  # price is not translated text

    action = await db_session.scalar(select(StaffAction).where(StaffAction.action == "menu.update"))
    assert "translations_reset" not in action.detail
