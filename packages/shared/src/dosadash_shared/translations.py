"""Menu localization schemas (Phase 7: Tamil-first).

Same architecture as nutrition enrichment: the LLM only DRAFTS customer-facing
text (dish name / description / category label in the target language), a
deterministic guardrail rejects anything that breaks hard invariants
(target script, numeral fidelity, invented currency, hallucinated item_ids),
and every draft lands as DRAFT — only an explicit owner approval can surface
a translation to customers.

Prices, allergens, veg/Jain flags and spice levels are NEVER translated —
they pass through from the canonical English row (single source of truth).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MENU_TRANSLATION_PROMPT_VERSION = "menu_translation_v1"

# Language registry — adding a language means adding it to ALL THREE maps
# (a key-free eval gate enforces this coherence).
SUPPORTED_TRANSLATION_LANGS: tuple[str, ...] = ("ta",)
TRANSLATION_LANG_NAMES: dict[str, str] = {"ta": "Tamil"}
# Unicode block per language: the guardrail requires translated names to
# actually be written in the target script (no plain-English echoes).
TRANSLATION_SCRIPT_RANGES: dict[str, tuple[int, int]] = {"ta": (0x0B80, 0x0BFF)}

MAX_TRANSLATION_ITEMS = 40  # api → ai bound per request (api chunks above this)
TRANSLATION_CHUNK_SIZE = 8  # items per LLM call (ai side fans out)


def _validate_lang(lang: str) -> str:
    if lang not in SUPPORTED_TRANSLATION_LANGS:
        raise ValueError(
            f"unsupported language {lang!r} (supported: {SUPPORTED_TRANSLATION_LANGS})"
        )
    return lang


class TranslationSourceItem(BaseModel):
    """One canonical (English) menu row, as context for the LLM."""

    item_id: int
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    category: str = Field(min_length=1, max_length=60)


class MenuTranslationRequest(BaseModel):
    """api → ai request: target language + the canonical rows to localize."""

    lang: str
    items: list[TranslationSourceItem] = Field(min_length=1, max_length=MAX_TRANSLATION_ITEMS)

    @field_validator("lang")
    @classmethod
    def _lang_supported(cls, v: str) -> str:
        return _validate_lang(v)


class TranslationDraft(BaseModel):
    """The LLM's structured output for ONE item — validated, never free-text."""

    item_id: int
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    category_label: str | None = Field(default=None, max_length=80)


class TranslationDraftBatch(BaseModel):
    """The LLM's structured output for one chunk (Hard Rule 3)."""

    translations: list[TranslationDraft] = Field(
        default_factory=list, max_length=TRANSLATION_CHUNK_SIZE * 2
    )


class TranslationRejection(BaseModel):
    """A requested item the guardrail refused to draft (with the reason)."""

    item_id: int
    reason: str


class MenuTranslationResponse(BaseModel):
    """ai → api: sanitized drafts + per-item rejections + provenance."""

    translations: list[TranslationDraft] = Field(default_factory=list)
    rejected: list[TranslationRejection] = Field(default_factory=list)
    model: str | None = None
    prompt_version: str = MENU_TRANSLATION_PROMPT_VERSION


class TranslationDraftIn(BaseModel):
    """Admin request: draft translations for specific items, or (item_ids
    omitted) for every item that has NO translation row yet — draft-all never
    silently resets rows a human already reviewed."""

    lang: str = "ta"
    item_ids: list[int] | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("lang")
    @classmethod
    def _lang_supported(cls, v: str) -> str:
        return _validate_lang(v)


class TranslationDraftFailure(BaseModel):
    item_id: int
    error: str


class TranslationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    lang: str
    name: str
    description: str | None = None
    category_label: str | None = None
    status: str
    model: str
    prompt_version: str
    reviewed_by: int | None = None
    updated_at: datetime | None = None


class TranslationDraftOut(BaseModel):
    drafted: list[TranslationOut] = []
    failed: list[TranslationDraftFailure] = []


class TranslationEditIn(BaseModel):
    """Owner edit — human authority, so no script/numeral guardrail here,
    but any edit resets the row to DRAFT for fresh review."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    category_label: str | None = Field(default=None, max_length=80)


class TranslationStatusIn(BaseModel):
    status: Literal["APPROVED", "REJECTED"]


class TranslationBulkStatusIn(BaseModel):
    """Bulk approve/reject all DRAFT rows for a language (or a specific list).
    Rows already at the target status are silently skipped."""

    lang: str = "ta"
    status: Literal["APPROVED", "REJECTED"]
    item_ids: list[int] | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("lang")
    @classmethod
    def _lang_supported(cls, v: str) -> str:
        return _validate_lang(v)


class TranslationBulkStatusOut(BaseModel):
    changed: int
    skipped: int
