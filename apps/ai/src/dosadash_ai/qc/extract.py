"""Dish-photo QC (Phase 7): kitchen photo → VLM observations → verdict.

Second VLM path (after invoice OCR, whose patterns this reuses): the image
goes through litellm as a data-URL part, vision-capable chain only. The
model reports OBSERVATIONS (dishes seen, visible issues); the verdict is
computed deterministically in verdict.py — earned confidence, the model
never grades its own homework.
"""

import logging

from dosadash_ai.invoice.extract import VISION_MODELS
from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_ai.qc.verdict import compute_result
from dosadash_shared import DISH_QC_PROMPT_VERSION, DishQCExtraction, DishQCIn, DishQCResult

logger = logging.getLogger(__name__)


def build_messages(request: DishQCIn) -> list[dict]:
    data_url = f"data:{request.mime_type};base64,{request.image_base64}"
    expected = ", ".join(request.expected_dishes)
    return [
        {"role": "system", "content": load_prompt(DISH_QC_PROMPT_VERSION)},
        {
            "role": "user",
            "content": [
                # The order context helps naming ("that's a rava dosa, not a
                # plain dosa") but rule 2 forbids inventing unseen dishes.
                {"type": "text", "text": f"The order contains: {expected}. Inspect this photo."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


async def qc_dish_photo(request: DishQCIn) -> DishQCResult:
    try:
        extraction, model = await structured_completion(
            messages=build_messages(request),
            response_model=DishQCExtraction,
            trace_name="dish_qc",
            prompt_version=DISH_QC_PROMPT_VERSION,
            session_id=request.session_id,
            models=VISION_MODELS,
            max_tokens=500,
        )
    except LLMError as exc:
        logger.warning("dish qc extraction failed: %s", exc)
        return compute_result(request.expected_dishes, None, error=str(exc)[:300])
    return compute_result(request.expected_dishes, extraction, model=model)
