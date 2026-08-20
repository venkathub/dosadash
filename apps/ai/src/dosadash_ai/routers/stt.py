"""Internal STT endpoint (Phase 7 — bot voice notes, proxied by the api).

POST /internal/stt — X-Internal-Token guarded. Returns a PII-redacted
transcript; the adapter echoes it and feeds it into the SAME order-agent
graph as typed text (voice is an input mode, not a new agent).
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.llm import LLMError
from dosadash_ai.llm.stt import InvalidAudio, transcribe
from dosadash_shared import SttIn, SttResult

router = APIRouter(prefix="/internal/stt", tags=["internal:stt"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("", response_model=SttResult)
async def stt(
    request: SttIn,
    x_internal_token: Annotated[str, Header()] = "",
) -> SttResult:
    _check_internal_token(x_internal_token)
    try:
        return await transcribe(request)
    except InvalidAudio as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}") from exc
