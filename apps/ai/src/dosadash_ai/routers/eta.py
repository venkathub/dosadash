"""Internal ETA endpoint (Phase 5 ML inference — CLAUDE.md repo layout puts
model scoring in apps/ai).

POST /internal/eta — X-Internal-Token guarded (api → ai, called at checkout).
Loads the exported ETA champion once (xgboost booster, ~ms scoring); the api
falls back to a heuristic if this endpoint is unavailable, so checkout never
blocks on the model.
"""

import logging
import secrets
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ml.eta.predict import EtaChampion, load_eta_champion, predict_eta_minutes
from dosadash_shared import EtaRequest, EtaResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/eta", tags=["internal:eta"])

IST = ZoneInfo("Asia/Kolkata")


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@lru_cache
def _champion() -> EtaChampion:
    return load_eta_champion(get_settings().model_dir)


@router.post("", response_model=EtaResponse)
async def predict_eta(
    request: EtaRequest,
    x_internal_token: Annotated[str, Header()] = "",
) -> EtaResponse:
    _check_internal_token(x_internal_token)
    try:
        model = _champion()
    except Exception as exc:  # missing/corrupt artifacts → api uses heuristic
        logger.warning("eta: champion unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="ETA model unavailable") from exc

    placed_at = request.placed_at or datetime.now(UTC)
    if placed_at.tzinfo is None:
        placed_at = placed_at.replace(tzinfo=UTC)
    when = placed_at.astimezone(IST).replace(tzinfo=None)  # model speaks IST
    minutes = predict_eta_minutes(
        model,
        max_prep=request.max_prep_minutes,
        total_qty=request.total_qty,
        n_lines=request.n_lines,
        when=when,
    )
    return EtaResponse(eta_minutes=minutes, model_version=model.version)
