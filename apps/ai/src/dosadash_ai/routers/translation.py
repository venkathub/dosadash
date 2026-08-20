"""Internal menu translation endpoint (api → ai) — Phase 7, Tamil-first.

POST /internal/translate/menu — guarded by X-Internal-Token. The LLM only
drafts customer-facing text; a deterministic guardrail (`sanitize_batch`)
enforces the invariants the model is merely told about:

- hallucinated item_ids are dropped, omissions are reported per item
- names must actually be in the target script (no plain-English echoes)
- names must carry exactly the source's numerals (pack sizes survive,
  invented numbers/prices don't)
- descriptions/category labels that break the rules are discarded (the
  item still drafts — name is the critical field), never invented

No PII flows through here (menu data only). Every draft lands api-side as
DRAFT: a human approves before anything is served (same trust model as
nutrition enrichment).
"""

import json
import re
import secrets
from collections import Counter
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.llm import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_shared import (
    MENU_TRANSLATION_PROMPT_VERSION,
    TRANSLATION_CHUNK_SIZE,
    TRANSLATION_LANG_NAMES,
    TRANSLATION_SCRIPT_RANGES,
    MenuTranslationRequest,
    MenuTranslationResponse,
    TranslationDraft,
    TranslationDraftBatch,
    TranslationRejection,
    TranslationSourceItem,
)

router = APIRouter(prefix="/internal/translate", tags=["internal:translation"])

_DIGIT_RUN = re.compile(r"\d+")


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def build_messages(lang: str, items: list[TranslationSourceItem]) -> list[dict[str, str]]:
    """System prompt from the versioned file + a compact JSON user payload."""
    payload = {
        "target_language": TRANSLATION_LANG_NAMES[lang],
        "lang_code": lang,
        "items": [
            {
                "item_id": item.item_id,
                "name": item.name,
                "description": item.description,
                "category": item.category,
            }
            for item in items
        ],
    }
    return [
        {"role": "system", "content": load_prompt(MENU_TRANSLATION_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


# ------------------------------------------------------------------ guardrail


def _digit_runs(text: str | None) -> Counter[str]:
    return Counter(_DIGIT_RUN.findall(text or ""))


def _in_target_script(text: str, lang: str) -> bool:
    low, high = TRANSLATION_SCRIPT_RANGES[lang]
    return any(low <= ord(ch) <= high for ch in text)


def _name_violation(source: TranslationSourceItem, name: str, lang: str) -> str | None:
    """Fatal checks — a bad name rejects the whole draft."""
    if not name:
        return "empty name"
    if not _in_target_script(name, lang):
        return f"name is not in {TRANSLATION_LANG_NAMES[lang]} script"
    if _digit_runs(name) != _digit_runs(source.name):
        return "name changes the numerals of the source (pack sizes must survive verbatim)"
    if "₹" in name and "₹" not in source.name:
        return "name invents a price"
    return None


def _clean_optional(source: TranslationSourceItem, text: str | None, lang: str) -> str | None:
    """Non-fatal fields: discard (→ None) rather than reject the item."""
    cleaned = (text or "").strip()
    if not cleaned or not _in_target_script(cleaned, lang):
        return None
    allowed_digits = _digit_runs(source.name) + _digit_runs(source.description)
    if _digit_runs(cleaned) - allowed_digits:  # invented numerals
        return None
    if "₹" in cleaned and "₹" not in (source.name + (source.description or "")):
        return None
    return cleaned


def sanitize_batch(
    items: list[TranslationSourceItem], batch: TranslationDraftBatch, lang: str
) -> tuple[list[TranslationDraft], list[TranslationRejection]]:
    """Re-anchor the LLM's drafts to the requested items (inventory-agent
    pattern: the request is authoritative, the model only wrote text)."""
    sources = {item.item_id: item for item in items}
    kept: dict[int, TranslationDraft] = {}
    reasons: dict[int, str] = {}
    for draft in batch.translations:
        source = sources.get(draft.item_id)
        if source is None:  # hallucinated item_id → drop
            continue
        if draft.item_id in kept or draft.item_id in reasons:  # duplicate → first wins
            continue
        name = draft.name.strip()
        violation = _name_violation(source, name, lang)
        if violation:
            reasons[draft.item_id] = violation
            continue
        kept[draft.item_id] = TranslationDraft(
            item_id=draft.item_id,
            name=name,
            description=_clean_optional(source, draft.description, lang),
            category_label=_clean_optional(source, draft.category_label, lang),
        )
    for item in items:  # omissions are reported, never fabricated
        if item.item_id not in kept and item.item_id not in reasons:
            reasons[item.item_id] = "missing from model output"
    ordered_kept = [kept[i.item_id] for i in items if i.item_id in kept]
    ordered_rejected = [
        TranslationRejection(item_id=i.item_id, reason=reasons[i.item_id])
        for i in items
        if i.item_id in reasons
    ]
    return ordered_kept, ordered_rejected


# ------------------------------------------------------------------- endpoint


def _chunks(items: list[TranslationSourceItem], size: int) -> Iterator[list[TranslationSourceItem]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@router.post("/menu", response_model=MenuTranslationResponse)
async def translate_menu(
    req: MenuTranslationRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> MenuTranslationResponse:
    _check_internal_token(x_internal_token)
    translations: list[TranslationDraft] = []
    rejected: list[TranslationRejection] = []
    model_used: str | None = None
    for chunk in _chunks(req.items, TRANSLATION_CHUNK_SIZE):
        try:
            parsed, model = await structured_completion(
                messages=build_messages(req.lang, chunk),
                response_model=TranslationDraftBatch,
                trace_name="translation.menu",
                prompt_version=MENU_TRANSLATION_PROMPT_VERSION,
                session_id=f"translate:{req.lang}",
                max_tokens=2500,
            )
        except LLMError as exc:  # one dead chunk doesn't sink the batch
            rejected.extend(
                TranslationRejection(item_id=i.item_id, reason=f"LLM chain failed: {exc}")
                for i in chunk
            )
            continue
        model_used = model
        kept, chunk_rejected = sanitize_batch(chunk, parsed, req.lang)
        translations.extend(kept)
        rejected.extend(chunk_rejected)
    if model_used is None:  # every chunk failed → the caller should know loudly
        raise HTTPException(status_code=502, detail="LLM chain failed for every batch")
    return MenuTranslationResponse(
        translations=translations,
        rejected=rejected,
        model=model_used,
        prompt_version=MENU_TRANSLATION_PROMPT_VERSION,
    )
