"""Recommendation serving tests — DB loaders are faked, embeddings are the
deterministic conftest fake, the champion is a hand-built toy. No network,
no provider keys."""

import json
from decimal import Decimal

import httpx
import numpy as np
import pytest

from dosadash_ai import config
from dosadash_ai.db import get_session
from dosadash_ai.recsys import serve
from dosadash_ai.recsys.serve import RecCandidate, flush_menu_embeddings, recommend
from dosadash_ml.recsys.predict import load_recsys_champion
from dosadash_shared import RecsRequest


async def fake_embed_texts(texts: list[str], **_: object) -> list[list[float]]:
    """Deterministic bag-of-words vectors (same scheme as apps/ai conftest;
    inlined — importing `conftest` cross-package is collection-order-fragile)."""
    import hashlib
    import math
    import re

    from dosadash_shared import EMBEDDING_DIM

    out = []
    for content in texts:
        vec = [0.0] * EMBEDDING_DIM
        for token in re.findall(r"[a-z0-9]+", content.lower()):
            digest = int(hashlib.sha256(token.encode()).hexdigest(), 16)
            vec[digest % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


def _menu() -> list[RecCandidate]:
    mk = lambda i, name, cat, veg=True, orderable=True, desc=None: RecCandidate(  # noqa: E731
        id=i,
        name=name,
        category=cat,
        price=Decimal("120.00"),
        is_veg=veg,
        description=desc,
        orderable=orderable,
    )
    return [
        mk(1, "Masala Dosa", "Dosa", desc="crisp dosa with potato masala"),
        mk(2, "Ghee Roast Dosa", "Dosa", desc="crisp ghee roast dosa"),
        mk(3, "Filter Coffee", "Beverages", desc="strong filter coffee with milk"),
        mk(4, "Chicken Biryani", "Biryani", veg=False, desc="spicy chicken biryani"),
        mk(5, "Podi Dosa", "Dosa", desc="dosa with gunpowder podi", orderable=False),  # 86'd
    ]


def _toy_champion(tmp_path):
    champ_dir = tmp_path / "recsys" / "champion"
    champ_dir.mkdir(parents=True)
    # Dosa items cluster on factor 0, biryani on factor 1, coffee in between.
    np.save(
        champ_dir / "item_factors.npy",
        np.array([[1.0, 0.0], [0.95, 0.05], [0.5, 0.5], [0.0, 1.0], [0.9, 0.1]]),
    )
    (champ_dir / "meta.json").write_text(
        json.dumps(
            {
                "model_version": "dosadash-recsys/v-test",
                "item_names": [
                    "Masala Dosa",
                    "Ghee Roast Dosa",
                    "Filter Coffee",
                    "Chicken Biryani",
                    "Podi Dosa",
                ],
                "popularity": [
                    "Masala Dosa",
                    "Filter Coffee",
                    "Ghee Roast Dosa",
                    "Chicken Biryani",
                    "Podi Dosa",
                ],
                "alpha": 5.0,
                "regularization": 0.1,
                "count_scale": "log1p",
            }
        )
    )
    return load_recsys_champion(tmp_path)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flush_menu_embeddings()

    async def fake_load_menu(session):
        return _menu()

    monkeypatch.setattr(serve, "load_menu", fake_load_menu)
    monkeypatch.setattr(serve, "embed_texts", fake_embed_texts)
    yield
    flush_menu_embeddings()


def _fake_history(counts):
    async def load(session, user_id):
        return counts

    return load


def _fake_popularity(ids):
    async def load(session):
        return ids

    return load


async def test_als_path_for_returning_user(monkeypatch, tmp_path):
    champion = _toy_champion(tmp_path)
    monkeypatch.setattr(serve, "_champion", lambda: champion)
    monkeypatch.setattr(serve, "load_history", _fake_history({"Chicken Biryani": 6.0}))
    resp = await recommend(None, RecsRequest(user_id=42, k=3))
    assert resp.source == "als"
    assert resp.model_version == "dosadash-recsys/v-test"
    names = [i.name for i in resp.items]
    assert "Podi Dosa" not in names  # 86'd items never recommended
    assert names[0] == "Chicken Biryani"  # collinear with the taste vector


async def test_als_skipped_when_history_unknown_to_model(monkeypatch, tmp_path):
    champion = _toy_champion(tmp_path)
    monkeypatch.setattr(serve, "_champion", lambda: champion)
    monkeypatch.setattr(serve, "load_history", _fake_history({"Off-Menu Special": 2.0}))
    monkeypatch.setattr(serve, "load_db_popularity", _fake_popularity([3, 1]))
    resp = await recommend(None, RecsRequest(user_id=42, k=2))
    assert resp.source == "popular"  # no cart → popularity, never fake personalization


async def test_embedding_cold_start_with_cart(monkeypatch):
    monkeypatch.setattr(serve, "_champion", lambda: None)  # no artifacts
    resp = await recommend(None, RecsRequest(user_id=None, cart_item_ids=[1], k=2))
    assert resp.source == "embedding"
    names = [i.name for i in resp.items]
    assert "Masala Dosa" not in names  # cart items excluded
    assert "Podi Dosa" not in names  # 86'd excluded
    # bag-of-words fake embedding: shared "dosa"/"crisp" tokens → dosa first
    assert names[0] == "Ghee Roast Dosa"


async def test_popularity_when_anonymous_and_empty_cart(monkeypatch):
    monkeypatch.setattr(serve, "_champion", lambda: None)
    monkeypatch.setattr(serve, "load_db_popularity", _fake_popularity([4, 3, 1]))
    resp = await recommend(None, RecsRequest(k=3))
    assert resp.source == "popular"
    assert [i.name for i in resp.items] == ["Chicken Biryani", "Filter Coffee", "Masala Dosa"]


async def test_embedding_failure_degrades_to_popularity(monkeypatch):
    async def boom(texts, **_):
        raise RuntimeError("provider down")

    monkeypatch.setattr(serve, "_champion", lambda: None)
    monkeypatch.setattr(serve, "embed_texts", boom)
    monkeypatch.setattr(serve, "load_db_popularity", _fake_popularity([3]))
    resp = await recommend(None, RecsRequest(cart_item_ids=[1], k=2))
    assert resp.source == "popular"


async def test_embedding_cache_reused_and_flushed(monkeypatch):
    calls = []

    async def counting_embed(texts, **kwargs):
        calls.append(list(texts))
        return await fake_embed_texts(texts)

    monkeypatch.setattr(serve, "_champion", lambda: None)
    monkeypatch.setattr(serve, "embed_texts", counting_embed)
    req = RecsRequest(cart_item_ids=[1], k=2)
    await recommend(None, req)
    await recommend(None, req)
    assert len(calls) == 1  # second request served from cache
    flush_menu_embeddings()  # menu cascade event
    await recommend(None, req)
    assert len(calls) == 2


# ------------------------------------------------------------------ endpoint


@pytest.fixture
async def ai_client(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    from dosadash_ai.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    config.get_settings.cache_clear()


async def test_recs_requires_internal_token(ai_client):
    resp = await ai_client.post("/internal/recs", json={"k": 3})
    assert resp.status_code == 403


async def test_recs_endpoint_happy_path(ai_client, monkeypatch):
    monkeypatch.setattr(serve, "_champion", lambda: None)
    monkeypatch.setattr(serve, "load_db_popularity", _fake_popularity([1, 2]))
    resp = await ai_client.post(
        "/internal/recs",
        json={"cart_item_ids": [], "k": 2},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "popular"
    assert len(body["items"]) == 2


# -------------------------------------------------------- checkout suggester


def _fake_combos(combos):
    async def load(session):
        return combos

    return load


async def test_checkout_combo_completion(monkeypatch, tmp_path):
    champion = _toy_champion(tmp_path)
    monkeypatch.setattr(serve, "_champion", lambda: champion)
    monkeypatch.setattr(serve, "load_history", _fake_history({"Masala Dosa": 5.0}))
    # cart holds Masala Dosa (1); combo = Masala Dosa + Filter Coffee (3)
    monkeypatch.setattr(
        serve, "load_approved_combos", _fake_combos([("Dosa Coffee Combo", [1, 3])])
    )
    resp = await serve.suggest_checkout(None, RecsRequest(user_id=7, cart_item_ids=[1], k=2))
    assert resp.source == "als"
    assert resp.suggestions[0].kind == "combo"
    assert resp.suggestions[0].name == "Filter Coffee"
    assert "Dosa Coffee Combo" in resp.suggestions[0].reason


async def test_checkout_pairing_uses_ranking_and_skips_86d(monkeypatch, tmp_path):
    champion = _toy_champion(tmp_path)
    monkeypatch.setattr(serve, "_champion", lambda: champion)
    # taste vector points at dosa factors → Filter Coffee still wins the
    # Beverages gap (only beverage), Podi Dosa (86'd) never appears
    monkeypatch.setattr(serve, "load_history", _fake_history({"Masala Dosa": 5.0}))
    monkeypatch.setattr(serve, "load_approved_combos", _fake_combos([]))
    resp = await serve.suggest_checkout(None, RecsRequest(user_id=7, cart_item_ids=[1], k=2))
    names = [s.name for s in resp.suggestions]
    assert "Podi Dosa" not in names
    assert all(s.kind == "pairing" for s in resp.suggestions)
    assert "Filter Coffee" in names  # fills the missing Beverages gap


async def test_checkout_empty_cart_no_suggestions(monkeypatch):
    monkeypatch.setattr(serve, "_champion", lambda: None)
    resp = await serve.suggest_checkout(None, RecsRequest(cart_item_ids=[], k=2))
    assert resp.suggestions == []


async def test_checkout_endpoint(ai_client, monkeypatch):
    monkeypatch.setattr(serve, "_champion", lambda: None)
    monkeypatch.setattr(serve, "load_db_popularity", _fake_popularity([3, 4]))
    monkeypatch.setattr(serve, "load_approved_combos", _fake_combos([]))
    resp = await ai_client.post(
        "/internal/recs/checkout",
        json={"cart_item_ids": [1], "k": 2},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # anonymous + cart → embedding ranking (fake embedder via autouse fixture)
    assert body["source"] == "embedding"
    assert all(s["kind"] in ("combo", "pairing") for s in body["suggestions"])


async def test_checkout_endpoint_requires_token(ai_client):
    resp = await ai_client.post("/internal/recs/checkout", json={"cart_item_ids": [1]})
    assert resp.status_code == 403
