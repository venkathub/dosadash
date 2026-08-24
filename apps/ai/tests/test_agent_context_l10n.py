"""Agent context localization tests (Phase 7): owner-APPROVED translated
names become item aliases in the menu payload; drafts never reach the
agent; the payload stays byte-identical when no translations exist."""

from sqlalchemy import text

from dosadash_ai.agent.context import load_context, menu_payload


async def test_approved_translations_become_aliases(agent_session):
    await agent_session.execute(
        text(
            "INSERT INTO menu_item_translations (item_id, lang, name, status) VALUES "
            "(1, 'ta', 'மசாலா தோசை', 'APPROVED'), "
            "(3, 'ta', 'ஃபில்டர் காபி', 'DRAFT')"  # draft: must NOT reach the agent
        )
    )
    await agent_session.commit()

    ctx = await load_context(agent_session, user_id=None)
    assert ctx.items[1].aliases == ("மசாலா தோசை",)
    assert ctx.items[3].aliases == ()  # DRAFT stays in the backoffice

    payload = {entry["item_id"]: entry for entry in menu_payload(ctx)}
    assert payload[1]["aliases"] == ["மசாலா தோசை"]
    assert payload[1]["name"] == "Masala Dosa"  # canonical name unchanged
    assert "aliases" not in payload[3]


async def test_payload_is_byte_stable_without_translations(agent_session):
    """Prefix-caching + live-gate invariant: with no approved translations
    the serialized menu context is exactly the pre-localization shape."""
    ctx = await load_context(agent_session, user_id=None)
    for entry in menu_payload(ctx):
        assert set(entry) == {
            "item_id",
            "name",
            "category",
            "price_inr",
            "veg",
            "jain_friendly",
            "spice",
            "allergens",
            "available",
        }
