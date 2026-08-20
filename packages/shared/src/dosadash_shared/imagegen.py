"""Menu image generation schemas (Phase 7): AI-drafted, owner-verified,
AI-labeled.

Same trust model as nutrition/translations: generation only ever produces a
DRAFT; an explicit owner approval sets `menu_items.image_url` (and marks the
row `image_ai = true`, surfaced as an "AI" badge — customers are never shown
an unlabeled synthetic photo). Rejection deletes the file.

Single provider (OpenAI image model via litellm) — no fallback chain, same
precedent as STT. Failure surfaces as a per-request error, never a 500.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MENU_IMAGE_PROMPT_VERSION = "menu_image_v1"


class MenuImageRequest(BaseModel):
    """api → ai: everything the image model needs, nothing it doesn't."""

    item_name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=2000)
    is_veg: bool = True


class MenuImageResult(BaseModel):
    """ai → api: the image plus full provenance (exact prompt included —
    the audit trail must show what the model was actually asked)."""

    image_b64: str = Field(min_length=100)  # PNG, base64
    model: str
    prompt_version: str = MENU_IMAGE_PROMPT_VERSION
    prompt: str = Field(max_length=4000)


class MenuImageDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    filename: str
    status: str
    model: str
    prompt_version: str
    reviewed_by: int | None = None
    updated_at: datetime | None = None
    url: str = ""  # filled by the router (/media/menu/{filename})


class MenuImageStatusIn(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
