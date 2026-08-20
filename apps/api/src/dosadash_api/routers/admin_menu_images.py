"""Admin AI menu images (Phase 7): generated drafts, owner-verified, AI-labeled.

POST /api/v1/admin/menu-images/{item_id}/generate — draft (or re-draft) a photo
GET  /api/v1/admin/menu-images                    — review queue
POST /api/v1/admin/menu-images/{item_id}/status   — approve/reject (human gate)

The image model never publishes anything: files land in the media dir as
DRAFT rows and only an explicit approval sets menu_items.image_url — always
together with image_ai = true, so the customer UI labels every synthetic
photo (docs/05: "AI-labeled" is part of the deliverable). Rejection deletes
the file; re-generation writes a NEW file and never clobbers a live one.
"""

import base64
import binascii
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.config import get_settings
from dosadash_api.db.models import MenuImageDraft, MenuItem, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    MenuImageDraftOut,
    MenuImageRequest,
    MenuImageStatusIn,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/menu-images", tags=["admin:menu-images"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AIClientDep = Annotated[AIClient, Depends(get_ai_client)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _menu_media_dir() -> Path:
    directory = Path(get_settings().media_dir) / "menu"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _url(filename: str) -> str:
    return f"/media/menu/{filename}"


def _out(draft: MenuImageDraft) -> MenuImageDraftOut:
    out = MenuImageDraftOut.model_validate(draft)
    out.url = _url(draft.filename)
    return out


@router.post("/{item_id}/generate", response_model=MenuImageDraftOut)
async def generate(
    item_id: int,
    session: SessionDep,
    ai: AIClientDep,
    admin: User = AdminUser,
) -> MenuImageDraftOut:
    """Draft (or re-draft) an AI photo. Slow (image model) but bounded."""
    item = await session.get(MenuItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    request = MenuImageRequest(
        item_name=item.name,
        category=item.category,
        description=item.description,
        is_veg=item.is_veg,
    )
    try:
        result = await ai.generate_menu_image(request)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        raw = base64.b64decode(result.image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=502, detail="AI returned undecodable image") from exc
    if not raw.startswith(_PNG_MAGIC):
        raise HTTPException(status_code=502, detail="AI returned a non-PNG payload")

    filename = f"item{item_id}-{secrets.token_hex(8)}.png"
    (_menu_media_dir() / filename).write_bytes(raw)

    draft = await session.get(MenuImageDraft, item_id)
    if draft is not None:
        # never clobber a live image: only delete the old file if nothing serves it
        old_path = _menu_media_dir() / draft.filename
        if item.image_url != _url(draft.filename):
            old_path.unlink(missing_ok=True)
    else:
        draft = MenuImageDraft(item_id=item_id)
        session.add(draft)
    draft.filename = filename
    draft.status = "DRAFT"  # re-generation always needs fresh review
    draft.model = result.model
    draft.prompt_version = result.prompt_version
    draft.prompt = result.prompt
    draft.reviewed_by = None
    audit.record(
        session,
        actor=admin,
        action="menu_image.generate",
        entity=f"menu_item:{item_id}",
        detail={"filename": filename, "model": result.model},
    )
    await session.commit()
    await session.refresh(draft)
    return _out(draft)


@router.get("", response_model=list[MenuImageDraftOut])
async def list_drafts(
    session: SessionDep,
    admin: User = AdminUser,
    status: str | None = None,
) -> list[MenuImageDraftOut]:
    stmt = select(MenuImageDraft).order_by(MenuImageDraft.item_id)
    if status:
        stmt = stmt.where(MenuImageDraft.status == status)
    rows = (await session.scalars(stmt)).all()
    return [_out(r) for r in rows]


@router.post("/{item_id}/status", response_model=MenuImageDraftOut)
async def set_status(
    item_id: int,
    body: MenuImageStatusIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> MenuImageDraftOut:
    """The human gate: approval publishes the photo (AI-labeled); rejection
    deletes the file — and unpublishes it if it was live."""
    draft = await session.get(MenuImageDraft, item_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="No image draft for this item")
    if draft.status == body.status:
        raise HTTPException(status_code=409, detail=f"Already {body.status}")
    item = await session.get(MenuItem, item_id)
    previous = draft.status
    draft.status = body.status
    draft.reviewed_by = admin.id
    if body.status == "APPROVED":
        item.image_url = _url(draft.filename)
        item.image_ai = True  # customers always see the AI label
    else:  # REJECTED
        if item.image_url == _url(draft.filename):
            item.image_url = None
            item.image_ai = False
        (_menu_media_dir() / draft.filename).unlink(missing_ok=True)
    audit.record(
        session,
        actor=admin,
        action="menu_image.status",
        entity=f"menu_item:{item_id}",
        detail={"from": previous, "to": body.status, "filename": draft.filename},
    )
    await session.commit()
    await session.refresh(draft)
    await events.publish_menu_event("menu.image", item_id=item_id, detail={"status": body.status})
    return _out(draft)
