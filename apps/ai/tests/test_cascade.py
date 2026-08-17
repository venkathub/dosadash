"""Event cascade tests (Hard Rule 4): menu events → menu_items re-embed."""

import pytest
from conftest import fake_embed_texts
from sqlalchemy import text

from dosadash_ai import cascade
from dosadash_ai.cascade import handle_menu_event, reembed_menu_item


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    monkeypatch.setattr(cascade, "embed_texts", fake_embed_texts)


@pytest.fixture
async def menu_table(rag_session):
    """Minimal menu_items table (columns the cascade touches) on the test DB;
    dropped afterwards so apps/api tests can create the real one cleanly."""
    await rag_session.execute(text("DROP TABLE IF EXISTS menu_items CASCADE"))
    await rag_session.execute(
        text(
            "CREATE TABLE menu_items (id bigint PRIMARY KEY, name text NOT NULL, "
            "category text NOT NULL, description text, embedding vector(1536))"
        )
    )
    await rag_session.execute(
        text(
            "INSERT INTO menu_items (id, name, category, description) VALUES "
            "(1, 'Masala Dosa', 'Dosa', 'Crisp dosa with spiced potato-onion masala')"
        )
    )
    await rag_session.commit()
    yield rag_session
    await rag_session.execute(text("DROP TABLE IF EXISTS menu_items CASCADE"))
    await rag_session.commit()


async def _embedding_set(session) -> bool:
    value = await session.scalar(text("SELECT embedding FROM menu_items WHERE id = 1"))
    return value is not None


async def test_menu_updated_reembeds_item(menu_table):
    assert not await _embedding_set(menu_table)
    await handle_menu_event(menu_table, {"type": "menu.updated", "item_id": 1, "detail": {}})
    assert await _embedding_set(menu_table)


async def test_menu_created_reembeds_item(menu_table):
    await handle_menu_event(menu_table, {"type": "menu.created", "item_id": 1, "detail": {}})
    assert await _embedding_set(menu_table)


async def test_missing_row_is_tolerated(menu_table):
    assert await reembed_menu_item(menu_table, 999) is False


async def test_irrelevant_events_do_not_touch_embeddings(menu_table):
    for payload in (
        {"type": "menu.deleted", "item_id": 1, "detail": {}},
        {"type": "menu.availability", "item_id": 1, "detail": {"is_available": False}},
        {"type": "combo.created", "detail": {"combo_id": 3}},  # catalog event, no item_id
        {"type": "menu.updated"},  # malformed: no item_id
    ):
        await handle_menu_event(menu_table, payload)
    assert not await _embedding_set(menu_table)


async def test_embedding_content_includes_name_and_category(menu_table, monkeypatch):
    seen: list[str] = []

    async def spy_embed(texts, **_):
        seen.extend(texts)
        return await fake_embed_texts(texts)

    monkeypatch.setattr(cascade, "embed_texts", spy_embed)
    await reembed_menu_item(menu_table, 1)
    assert seen == ["Masala Dosa — Dosa. Crisp dosa with spiced potato-onion masala"]
