"""Internal dish-photo QC endpoint (Phase 7).

POST /internal/qc/dish — X-Internal-Token guarded (api → ai). Never 5xxs on
model failure: an unreadable photo comes back as verdict=UNREADABLE so the
KDS can tell staff to retake it.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.qc.extract import qc_dish_photo
from dosadash_shared import DishQCIn, DishQCResult

router = APIRouter(prefix="/internal/qc", tags=["internal:qc"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/dish", response_model=DishQCResult)
async def qc_dish(
    request: DishQCIn,
    x_internal_token: Annotated[str, Header()] = "",
) -> DishQCResult:
    _check_internal_token(x_internal_token)
    return await qc_dish_photo(request)
