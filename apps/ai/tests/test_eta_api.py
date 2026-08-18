"""/internal/eta: token guard, champion scoring, degradation."""

import httpx
import pytest

from dosadash_ai import config

pytest.importorskip("xgboost")

HEADERS = {"X-Internal-Token": "test-internal-token"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from dosadash_ai.routers import eta as eta_mod

    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    eta_mod._champion.cache_clear()
    yield
    config.get_settings.cache_clear()
    eta_mod._champion.cache_clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


PAYLOAD = {"max_prep_minutes": 20, "total_qty": 3, "n_lines": 2}


async def test_token_guard(ai_client):
    assert (await ai_client.post("/internal/eta", json=PAYLOAD)).status_code == 403
    bad = {"X-Internal-Token": "nope"}
    assert (await ai_client.post("/internal/eta", json=PAYLOAD, headers=bad)).status_code == 403


async def test_predicts_from_committed_champion(ai_client):
    resp = await ai_client.post("/internal/eta", json=PAYLOAD, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert 15 <= body["eta_minutes"] <= 120
    assert body["model_version"].startswith("dosadash-eta/v")


async def test_degrades_to_503_when_artifacts_missing(ai_client, monkeypatch):
    monkeypatch.setenv("AI_MODEL_DIR", "/nonexistent")
    config.get_settings.cache_clear()
    resp = await ai_client.post("/internal/eta", json=PAYLOAD, headers=HEADERS)
    assert resp.status_code == 503
