"""Evaluation setup must roll back exact DB state, including on cancellation."""

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evals/suites"))
import _harness


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_order_case_restores_original_state_on_failure(db_session, monkeypatch, failure):
    # Original kitchen is paused; translation already exists and must not be deleted.
    await db_session.execute(
        text(
            "INSERT INTO settings (id, kitchen_paused, delivery_pincodes) VALUES (1, true, '{}') "
            "ON CONFLICT (id) DO UPDATE SET kitchen_paused=true"
        )
    )
    item = await db_session.scalar(text("SELECT id FROM menu_items WHERE name='Masala Dosa'"))
    await db_session.execute(
        text(
            "INSERT INTO menu_item_translations (item_id, lang, name, status, "
            "model, prompt_version) "
            "VALUES (:i, 'ta', 'Original translation', 'APPROVED', 'human', 'original')"
        ),
        {"i": item},
    )
    await db_session.commit()

    async def fail(session, request):
        assert not await session.scalar(
            text("SELECT is_available FROM menu_items WHERE id=:i"), {"i": item}
        )
        raise failure()

    monkeypatch.setattr(_harness, "run_turn", fail)
    case = {
        "id": "isolation",
        "message": "hello",
        "history": [],
        "draft": None,
        "kitchen": "paused",
        "setup": {
            "make_unavailable": ["Masala Dosa"],
            "seed_translations": [{"name": "Masala Dosa", "lang": "ta", "text": "Temporary"}],
        },
    }
    with pytest.raises(failure):
        await _harness.run_case(db_session, case)
    assert await db_session.scalar(text("SELECT kitchen_paused FROM settings WHERE id=1")) is True
    assert (
        await db_session.scalar(
            text("SELECT is_available FROM menu_items WHERE id=:i"), {"i": item}
        )
        is True
    )
    assert (
        await db_session.scalar(
            text("SELECT name FROM menu_item_translations WHERE item_id=:i"), {"i": item}
        )
        == "Original translation"
    )
