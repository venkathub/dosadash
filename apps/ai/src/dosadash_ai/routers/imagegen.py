"""Internal menu image generation (Phase 7): AI-drafted, owner-verified,
AI-labeled dish photos.

POST /internal/imagegen/menu-item — X-Internal-Token guarded. Calls the
image model through litellm (Hard Rule 1; Hard Rule 7: API inference only —
nothing runs on the VPS). Single provider, no fallback chain (STT
precedent: OpenAI is the only image-capable provider in the stack); failure
surfaces as 502 and the api reports it per item.

The result is ONLY ever a draft: the api stores it for owner review and
nothing reaches `menu_items.image_url` without an explicit approval.
"""

import logging
import secrets
from typing import Annotated

import litellm
from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.llm import LLMError
from dosadash_ai.prompts import load_prompt
from dosadash_shared import MENU_IMAGE_PROMPT_VERSION, MenuImageRequest, MenuImageResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/imagegen", tags=["internal:imagegen"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def build_prompt(req: MenuImageRequest) -> str:
    """Versioned style contract + the dish facts (nothing else — the model
    never sees prices, and the veg flag is stated explicitly)."""
    lines = [
        f"Dish: {req.item_name}",
        f"Category: {req.category}",
        f"Vegetarian: {'yes — strictly no meat, fish or egg' if req.is_veg else 'no'}",
    ]
    if req.description:
        lines.append(f"Description: {req.description}")
    return load_prompt(MENU_IMAGE_PROMPT_VERSION) + "\n".join(lines)


async def generate_image(req: MenuImageRequest) -> MenuImageResult:
    """One bounded image generation call, traced to Langfuse (Hard Rule 6)."""
    settings = get_settings()
    prompt = build_prompt(req)
    try:
        # NB: no response_format param — the current OpenAI images API
        # rejects it (gpt-image-1 always returns b64_json).
        response = await litellm.aimage_generation(
            model=settings.image_model,
            prompt=prompt,
            n=1,
            size=settings.image_size,
            quality=settings.image_quality,
            metadata={
                "generation_name": "imagegen.menu_item",
                "session_id": f"imagegen:{req.item_name}",
                "tags": [MENU_IMAGE_PROMPT_VERSION],
            },
        )
    except Exception as exc:  # noqa: BLE001 — single provider, normalized for callers
        logger.warning("image generation failed on %s: %s", settings.image_model, exc)
        raise LLMError(f"image generation failed on {settings.image_model}: {exc}") from exc

    image_b64 = (response.data[0].b64_json or "") if response.data else ""
    if not image_b64:
        raise LLMError(f"{settings.image_model} returned no image data")
    return MenuImageResult(
        image_b64=image_b64,
        model=settings.image_model,
        prompt_version=MENU_IMAGE_PROMPT_VERSION,
        prompt=prompt[:4000],
    )


@router.post("/menu-item", response_model=MenuImageResult)
async def menu_item_image(
    req: MenuImageRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> MenuImageResult:
    _check_internal_token(x_internal_token)
    try:
        return await generate_image(req)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
