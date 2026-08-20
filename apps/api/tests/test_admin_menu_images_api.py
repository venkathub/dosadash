"""Admin AI menu-image tests — AI service mocked via dependency override,
files written to an isolated tmp media dir."""

import base64

import pytest
from sqlalchemy import select

from dosadash_api import config
from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import MenuImageDraft, MenuItem, StaffAction, User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import MenuImageResult, Role

IMAGES = "/api/v1/admin/menu-images"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
PNG_B64 = base64.b64encode(PNG).decode()


class FakeAIClient:
    def __init__(self, image_b64: str = PNG_B64, fail: bool = False) -> None:
        self.requests = []
        self.image_b64 = image_b64
        self.fail = fail

    async def generate_menu_image(self, request) -> MenuImageResult:
        self.requests.append(request)
        if self.fail:
            raise AIServiceError("AI service call failed: boom")
        return MenuImageResult(
            image_b64=self.image_b64,
            model="gpt-image-1",
            prompt="style + Dish: " + request.item_name,
        )


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("API_MEDIA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


@pytest.fixture
def fake_ai(client, media_dir):
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
    return await _login_as(db_session, "+919555559201", Role.ADMIN)


async def _dosa_id(db_session) -> int:
    return await db_session.scalar(select(MenuItem.id).where(MenuItem.name == "Masala Dosa"))


async def test_images_rbac(client, db_session):
    assert (await client.get(IMAGES)).status_code == 401
    kitchen = await _login_as(db_session, "+919555559202", Role.KITCHEN_STAFF)
    assert (await client.get(IMAGES, headers=kitchen)).status_code == 403


async def test_generate_creates_draft_file_and_audit(client, admin, fake_ai, media_dir, db_session):
    dosa = await _dosa_id(db_session)
    resp = await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DRAFT"
    assert body["url"].startswith("/media/menu/")
    assert (media_dir / "menu" / body["filename"]).read_bytes() == PNG
    assert fake_ai.requests[0].item_name == "Masala Dosa"

    # draft is NOT public — image_url untouched until approval
    item = await db_session.get(MenuItem, dosa)
    await db_session.refresh(item)
    assert item.image_url is None
    assert item.image_ai is False

    action = await db_session.scalar(
        select(StaffAction).where(StaffAction.action == "menu_image.generate")
    )
    assert action.detail["filename"] == body["filename"]

    # unknown item 404
    assert (await client.post(f"{IMAGES}/999999/generate", headers=admin)).status_code == 404


async def test_approve_publishes_ai_labeled(client, admin, fake_ai, media_dir, db_session):
    dosa = await _dosa_id(db_session)
    draft = (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).json()

    approved = await client.post(
        f"{IMAGES}/{dosa}/status", headers=admin, json={"status": "APPROVED"}
    )
    assert approved.status_code == 200
    assert approved.json()["reviewed_by"] is not None

    # public menu now serves the image WITH the AI label
    menu = {m["id"]: m for m in (await client.get("/api/v1/menu")).json()}
    assert menu[dosa]["image_url"] == f"/media/menu/{draft['filename']}"
    assert menu[dosa]["image_ai"] is True

    # same-status refused; unknown item 404
    resp = await client.post(f"{IMAGES}/{dosa}/status", headers=admin, json={"status": "APPROVED"})
    assert resp.status_code == 409
    resp = await client.post(f"{IMAGES}/999999/status", headers=admin, json={"status": "APPROVED"})
    assert resp.status_code == 404


async def test_reject_deletes_file_and_unpublishes(client, admin, fake_ai, media_dir, db_session):
    dosa = await _dosa_id(db_session)
    draft = (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).json()
    await client.post(f"{IMAGES}/{dosa}/status", headers=admin, json={"status": "APPROVED"})

    rejected = await client.post(
        f"{IMAGES}/{dosa}/status", headers=admin, json={"status": "REJECTED"}
    )
    assert rejected.status_code == 200
    assert not (media_dir / "menu" / draft["filename"]).exists()  # file gone

    item = await db_session.get(MenuItem, dosa)
    await db_session.refresh(item)
    assert item.image_url is None  # unpublished
    assert item.image_ai is False


async def test_regenerate_never_clobbers_the_live_image(
    client, admin, fake_ai, media_dir, db_session
):
    dosa = await _dosa_id(db_session)
    first = (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).json()
    await client.post(f"{IMAGES}/{dosa}/status", headers=admin, json={"status": "APPROVED"})

    second = (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).json()
    assert second["filename"] != first["filename"]
    assert second["status"] == "DRAFT"  # fresh review required
    # the approved file is still being served → must still exist
    assert (media_dir / "menu" / first["filename"]).exists()
    assert (media_dir / "menu" / second["filename"]).exists()

    item = await db_session.get(MenuItem, dosa)
    await db_session.refresh(item)
    assert item.image_url == f"/media/menu/{first['filename']}"  # live image untouched


async def test_generate_survives_ai_outage_and_bad_payloads(client, admin, media_dir, db_session):
    from dosadash_api.main import app

    dosa = await _dosa_id(db_session)
    app.dependency_overrides[get_ai_client] = lambda: FakeAIClient(fail=True)
    try:
        assert (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).status_code == 502
        not_png = base64.b64encode(b"GIF89a" + b"\x00" * 120).decode()
        app.dependency_overrides[get_ai_client] = lambda: FakeAIClient(image_b64=not_png)
        assert (await client.post(f"{IMAGES}/{dosa}/generate", headers=admin)).status_code == 502
    finally:
        app.dependency_overrides.pop(get_ai_client, None)
    assert await db_session.scalar(select(MenuImageDraft)) is None  # nothing persisted
