"""Admin menu localization (Phase 7, Tamil-first): LLM drafts, owner-verified.

POST  /api/v1/admin/translations/draft              — draft (or re-draft) translations
POST  /api/v1/admin/translations/bulk-status        — bulk approve/reject all DRAFTs
GET   /api/v1/admin/translations                    — review queue (filter lang/status)
PATCH /api/v1/admin/translations/{id}/{lang}        — owner edit (resets to DRAFT)
POST  /api/v1/admin/translations/{id}/{lang}/status — approve/reject (human gate)

Trust model mirrors nutrition enrichment: the LLM never publishes anything —
drafts land as DRAFT and only an explicit approval will let a later slice
serve them to customers. Draft-all (no item_ids) only fills GAPS: it never
silently re-drafts rows a human already reviewed; targeting explicit
item_ids is the deliberate way to re-draft (and resets them to DRAFT).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import MenuItem, MenuItemTranslation, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    MAX_TRANSLATION_ITEMS,
    SUPPORTED_TRANSLATION_LANGS,
    MenuTranslationRequest,
    Role,
    TranslationBulkStatusIn,
    TranslationBulkStatusOut,
    TranslationDraftFailure,
    TranslationDraftIn,
    TranslationDraftOut,
    TranslationEditIn,
    TranslationOut,
    TranslationSourceItem,
    TranslationStatusIn,
)

router = APIRouter(prefix="/api/v1/admin/translations", tags=["admin:translations"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AIClientDep = Annotated[AIClient, Depends(get_ai_client)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


def _check_lang(lang: str) -> None:
    if lang not in SUPPORTED_TRANSLATION_LANGS:
        raise HTTPException(status_code=422, detail=f"Unsupported language {lang!r}")


async def _get_or_404(session: AsyncSession, item_id: int, lang: str) -> MenuItemTranslation:
    _check_lang(lang)
    record = await session.get(MenuItemTranslation, (item_id, lang))
    if record is None:
        raise HTTPException(status_code=404, detail="No translation for this item/language")
    return record


@router.post("/bulk-status", response_model=TranslationBulkStatusOut)
async def bulk_status(
    body: TranslationBulkStatusIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> TranslationBulkStatusOut:
    """Bulk approve or reject all rows for a language (or an explicit list).
    Rows already at the target status are silently skipped."""
    stmt = select(MenuItemTranslation).where(MenuItemTranslation.lang == body.lang)
    if body.item_ids is not None:
        stmt = stmt.where(MenuItemTranslation.item_id.in_(set(body.item_ids)))
    rows = (await session.scalars(stmt)).all()

    changed_ids: list[int] = []
    skipped = 0
    for record in rows:
        if record.status == body.status:
            skipped += 1
            continue
        record.status = body.status
        record.reviewed_by = admin.id
        changed_ids.append(record.item_id)

    if changed_ids:
        audit.record(
            session,
            actor=admin,
            action="translation.bulk_status",
            entity="translation",
            detail={"lang": body.lang, "status": body.status, "item_ids": changed_ids},
        )
        await session.commit()
        for item_id in changed_ids:
            await events.publish_menu_event(
                "menu.translation", item_id=item_id, detail={"lang": body.lang, "status": body.status}
            )

    return TranslationBulkStatusOut(changed=len(changed_ids), skipped=skipped)


@router.post("/draft", response_model=TranslationDraftOut)
async def draft(
    body: TranslationDraftIn,
    session: SessionDep,
    ai: AIClientDep,
    admin: User = AdminUser,
) -> TranslationDraftOut:
    """Draft translations. Per-item failures don't sink the batch."""
    out = TranslationDraftOut()
    if body.item_ids is not None:
        items = (
            await session.scalars(
                select(MenuItem).where(MenuItem.id.in_(set(body.item_ids))).order_by(MenuItem.id)
            )
        ).all()
        found = {m.id for m in items}
        out.failed.extend(
            TranslationDraftFailure(item_id=item_id, error="unknown item id")
            for item_id in body.item_ids
            if item_id not in found
        )
    else:  # gap-fill: only items with no row for this language yet
        translated = select(MenuItemTranslation.item_id).where(
            MenuItemTranslation.lang == body.lang
        )
        items = (
            await session.scalars(
                select(MenuItem).where(MenuItem.id.not_in(translated)).order_by(MenuItem.id)
            )
        ).all()

    sources = [
        TranslationSourceItem(
            item_id=m.id, name=m.name, description=m.description, category=m.category
        )
        for m in items
    ]
    for start in range(0, len(sources), MAX_TRANSLATION_ITEMS):
        chunk = sources[start : start + MAX_TRANSLATION_ITEMS]
        try:
            response = await ai.translate_menu(MenuTranslationRequest(lang=body.lang, items=chunk))
        except AIServiceError as exc:
            out.failed.extend(
                TranslationDraftFailure(item_id=s.item_id, error=str(exc)) for s in chunk
            )
            continue
        out.failed.extend(
            TranslationDraftFailure(item_id=r.item_id, error=r.reason) for r in response.rejected
        )
        for translation in response.translations:
            record = await session.get(MenuItemTranslation, (translation.item_id, body.lang))
            if record is None:
                record = MenuItemTranslation(item_id=translation.item_id, lang=body.lang)
                session.add(record)
            record.name = translation.name
            record.description = translation.description
            record.category_label = translation.category_label
            record.status = "DRAFT"  # re-drafting always needs fresh review
            record.model = response.model or "unknown"
            record.prompt_version = response.prompt_version
            record.reviewed_by = None
            out.drafted.append(TranslationOut.model_validate(record))

    if out.drafted:
        audit.record(
            session,
            actor=admin,
            action="translation.draft",
            entity="translation",
            detail={"lang": body.lang, "item_ids": [t.item_id for t in out.drafted]},
        )
        await session.commit()
    return out


@router.get("", response_model=list[TranslationOut])
async def list_translations(
    session: SessionDep,
    admin: User = AdminUser,
    lang: str | None = None,
    status: str | None = None,
) -> list[TranslationOut]:
    stmt = select(MenuItemTranslation).order_by(
        MenuItemTranslation.lang, MenuItemTranslation.item_id
    )
    if lang:
        stmt = stmt.where(MenuItemTranslation.lang == lang)
    if status:
        stmt = stmt.where(MenuItemTranslation.status == status)
    rows = (await session.scalars(stmt)).all()
    return [TranslationOut.model_validate(r) for r in rows]


@router.patch("/{item_id}/{lang}", response_model=TranslationOut)
async def edit_translation(
    item_id: int,
    lang: str,
    body: TranslationEditIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> TranslationOut:
    """Owner edit (human authority — no script guardrail), but any edit
    resets the row to DRAFT so what's served is always what was reviewed."""
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="Nothing to edit")
    record = await _get_or_404(session, item_id, lang)
    changed: dict[str, str | None] = {}
    for field in body.model_fields_set:
        value = getattr(body, field)
        setattr(record, field, value.strip() if isinstance(value, str) else value)
        changed[field] = getattr(record, field)
    record.status = "DRAFT"
    record.reviewed_by = None
    audit.record(
        session,
        actor=admin,
        action="translation.edit",
        entity=f"menu_item:{item_id}",
        detail={"lang": lang, "changed": changed},
    )
    await session.commit()
    await session.refresh(record)
    return TranslationOut.model_validate(record)


@router.post("/{item_id}/{lang}/status", response_model=TranslationOut)
async def set_status(
    item_id: int,
    lang: str,
    body: TranslationStatusIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> TranslationOut:
    """The human gate: only APPROVED translations will ever reach customers."""
    record = await _get_or_404(session, item_id, lang)
    if record.status == body.status:
        raise HTTPException(status_code=409, detail=f"Already {body.status}")
    previous = record.status
    record.status = body.status
    record.reviewed_by = admin.id
    audit.record(
        session,
        actor=admin,
        action="translation.status",
        entity=f"menu_item:{item_id}",
        detail={"lang": lang, "from": previous, "to": body.status},
    )
    await session.commit()
    await session.refresh(record)
    await events.publish_menu_event(
        "menu.translation", item_id=item_id, detail={"lang": lang, "status": body.status}
    )
    return TranslationOut.model_validate(record)
