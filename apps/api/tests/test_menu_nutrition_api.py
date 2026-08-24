"""Public menu protein surfacing: `protein_g` rides the summary payload so the
customer menu can offer a high-protein filter — owner-APPROVED estimates only,
drafts never serve, and an unscored dish stays null (never zero)."""

from sqlalchemy import select

from dosadash_api.db.models import MenuItem, NutritionEstimateRecord

MENU = "/api/v1/menu"


async def _item_id(db_session, name: str) -> int:
    return await db_session.scalar(select(MenuItem.id).where(MenuItem.name == name))


async def _add_nutrition(
    db_session,
    item_id: int,
    *,
    status: str = "APPROVED",
    protein_g: float | None = 24.5,
) -> None:
    estimate = {
        "calories_kcal": 520.0,
        "carbs_g": 60.0,
        "fat_g": 18.0,
        "fiber_g": 4.0,
        "per": "serving",
        "confidence": 0.8,
    }
    if protein_g is not None:
        estimate["protein_g"] = protein_g
    db_session.add(
        NutritionEstimateRecord(
            item_id=item_id,
            estimate=estimate,
            status=status,
            model="gpt-4o-mini",
            prompt_version="nutrition_v1",
        )
    )
    await db_session.commit()


async def _by_name(client, params: dict | None = None) -> dict[str, dict]:
    resp = await client.get(MENU, params=params or {})
    assert resp.status_code == 200
    return {i["name"]: i for i in resp.json()}


async def test_summary_serves_approved_protein(client, db_session):
    await _add_nutrition(db_session, await _item_id(db_session, "Chicken Biryani"))
    items = await _by_name(client)
    assert items["Chicken Biryani"]["protein_g"] == 24.5


async def test_summary_hides_draft_protein(client, db_session):
    await _add_nutrition(db_session, await _item_id(db_session, "Chicken Biryani"), status="DRAFT")
    items = await _by_name(client)
    assert items["Chicken Biryani"]["protein_g"] is None


async def test_summary_hides_rejected_protein(client, db_session):
    await _add_nutrition(
        db_session, await _item_id(db_session, "Chicken Biryani"), status="REJECTED"
    )
    items = await _by_name(client)
    assert items["Chicken Biryani"]["protein_g"] is None


async def test_unscored_dish_is_null_not_zero(client, db_session):
    await _add_nutrition(db_session, await _item_id(db_session, "Chicken Biryani"))
    items = await _by_name(client)
    # Masala Dosa has no estimate at all — the UI must be able to tell
    # "unknown" apart from "zero grams" and make no claim.
    assert items["Masala Dosa"]["protein_g"] is None


async def test_malformed_estimate_degrades_to_null(client, db_session):
    """A protein-less/garbled estimate must not 500 the menu (Rule: nice-to-have
    data degrades, the page never dies)."""
    item_id = await _item_id(db_session, "Chicken Biryani")
    await _add_nutrition(db_session, item_id, protein_g=None)
    items = await _by_name(client)
    assert items["Chicken Biryani"]["protein_g"] is None


async def test_protein_survives_localized_menu(client, db_session):
    """Nutrition is canonical data — it does not depend on the served language."""
    await _add_nutrition(db_session, await _item_id(db_session, "Chicken Biryani"))
    items = await _by_name(client, {"lang": "ta"})
    assert items["Chicken Biryani"]["protein_g"] == 24.5


async def test_detail_protein_matches_the_list(client, db_session):
    item_id = await _item_id(db_session, "Chicken Biryani")
    await _add_nutrition(db_session, item_id)
    resp = await client.get(f"{MENU}/items/{item_id}")
    body = resp.json()
    assert body["protein_g"] == 24.5
    assert body["nutrition"]["protein_g"] == 24.5


async def test_detail_draft_protein_stays_backoffice(client, db_session):
    item_id = await _item_id(db_session, "Chicken Biryani")
    await _add_nutrition(db_session, item_id, status="DRAFT")
    body = (await client.get(f"{MENU}/items/{item_id}")).json()
    assert body["protein_g"] is None
    assert body["nutrition"] is None
