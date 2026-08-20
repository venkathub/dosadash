"""Menu image generation tests (Phase 7) — litellm always mocked (CI never
calls providers). Prompt/schema contract gates live in
evals/suites/test_imagegen_assets.py."""

import base64

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.routers import imagegen
from dosadash_shared import MenuImageRequest

GENERATE = "/internal/imagegen/menu-item"
FAKE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 120).decode()


class FakeImageResponse:
    def __init__(self, b64: str | None) -> None:
        self.data = [type("D", (), {"b64_json": b64})()] if b64 is not None else []


def _req() -> dict:
    return MenuImageRequest(
        item_name="Masala Dosa",
        category="Dosa",
        description="Crisp dosa with potato masala",
        is_veg=True,
    ).model_dump(mode="json")


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_generate_requires_internal_token(ai_client):
    assert (await ai_client.post(GENERATE, json=_req())).status_code == 403
    resp = await ai_client.post(GENERATE, json=_req(), headers={"X-Internal-Token": "wrong"})
    assert resp.status_code == 403


async def test_generate_end_to_end_mocked(ai_client, monkeypatch):
    seen: dict = {}

    async def fake_aimage_generation(**kwargs):
        seen.update(kwargs)
        return FakeImageResponse(FAKE_PNG_B64)

    monkeypatch.setattr(imagegen.litellm, "aimage_generation", fake_aimage_generation)
    resp = await ai_client.post(
        GENERATE, json=_req(), headers={"X-Internal-Token": "test-internal-token"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["image_b64"] == FAKE_PNG_B64
    assert body["model"] == "gpt-image-1"
    assert body["prompt_version"] == "menu_image_v1"
    # the model saw the style contract AND the dish facts
    assert "ABSOLUTELY NO text" in seen["prompt"]
    assert "Dish: Masala Dosa" in seen["prompt"]
    assert seen["quality"] == "low"  # cost control travels with the call
    assert seen["metadata"]["tags"] == ["menu_image_v1"]  # Hard Rule 6


async def test_generate_502_when_provider_fails(ai_client, monkeypatch):
    async def fake_aimage_generation(**kwargs):
        raise RuntimeError("content policy / provider down")

    monkeypatch.setattr(imagegen.litellm, "aimage_generation", fake_aimage_generation)
    resp = await ai_client.post(
        GENERATE, json=_req(), headers={"X-Internal-Token": "test-internal-token"}
    )
    assert resp.status_code == 502


async def test_generate_502_when_no_image_data(ai_client, monkeypatch):
    async def fake_aimage_generation(**kwargs):
        return FakeImageResponse(None)

    monkeypatch.setattr(imagegen.litellm, "aimage_generation", fake_aimage_generation)
    resp = await ai_client.post(
        GENERATE, json=_req(), headers={"X-Internal-Token": "test-internal-token"}
    )
    assert resp.status_code == 502
