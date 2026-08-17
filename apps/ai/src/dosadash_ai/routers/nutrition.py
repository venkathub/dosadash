"""Internal nutrition estimation endpoint (api → ai).

POST /internal/nutrition/estimate — guarded by X-Internal-Token (same
pattern as bot→api). Input is the dish + its recipe mapping; output is a
validated NutritionEstimate (Hard Rule 3). No PII flows through here.
"""

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.llm import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_shared import (
    NUTRITION_PROMPT_VERSION,
    NutritionEstimate,
    NutritionEstimateRequest,
    NutritionEstimateResponse,
)

router = APIRouter(prefix="/internal/nutrition", tags=["internal:nutrition"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def build_messages(req: NutritionEstimateRequest) -> list[dict[str, str]]:
    """System prompt from the versioned file + a compact JSON user payload."""
    dish = {
        "name": req.item_name,
        "category": req.category,
        "description": req.description,
        "is_veg": req.is_veg,
        "recipe": [
            {"ingredient": line.name, "qty": str(line.qty), "unit": line.unit}
            for line in req.recipe
        ],
    }
    return [
        {"role": "system", "content": load_prompt(NUTRITION_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(dish, ensure_ascii=False)},
    ]


@router.post("/estimate", response_model=NutritionEstimateResponse)
async def estimate(
    req: NutritionEstimateRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> NutritionEstimateResponse:
    _check_internal_token(x_internal_token)
    try:
        parsed, model = await structured_completion(
            messages=build_messages(req),
            response_model=NutritionEstimate,
            trace_name="nutrition.estimate",
            prompt_version=NUTRITION_PROMPT_VERSION,
            session_id=f"nutrition:{req.item_name}",
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM chain failed: {exc}") from exc
    return NutritionEstimateResponse(
        estimate=parsed, model=model, prompt_version=NUTRITION_PROMPT_VERSION
    )
