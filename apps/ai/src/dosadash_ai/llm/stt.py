"""Speech-to-text (Phase 7): Telegram voice notes → transcript via litellm.

Hard Rule 1: Groq Whisper is called through litellm.atranscription, never
the Groq SDK. Hard Rule 7: API inference only — no local Whisper on the
4 GB VPS. Hard Rule 8: audio itself cannot be redacted (same trade-off as
invoice images going to the VLM), so the *transcript* is phone-redacted
before it is returned — callers only ever see/log/forward redacted text.

No fallback chain here: Groq is the only STT-capable provider in the stack.
Failure surfaces as LLMError; adapters degrade to "please type instead".
"""

import base64
import binascii
import io
import logging

import litellm

from dosadash_ai.config import get_settings
from dosadash_ai.llm.client import LLMError
from dosadash_ai.redaction import redact_phones
from dosadash_shared import SttIn, SttResult

logger = logging.getLogger(__name__)

# Whisper infers the codec from the filename extension litellm forwards.
_FILENAME_BY_MIME = {
    "audio/ogg": "voice.ogg",
    "audio/mpeg": "voice.mp3",
    "audio/mp4": "voice.m4a",
    "audio/wav": "voice.wav",
    "audio/webm": "voice.webm",
}


class InvalidAudio(Exception):
    """The payload is not decodable audio (caller error → 422)."""


def decode_audio(request: SttIn) -> io.BytesIO:
    """Base64 → named buffer (litellm needs a filename for codec detection)."""
    try:
        raw = base64.b64decode(request.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidAudio("audio_base64 is not valid base64") from exc
    if not raw:
        raise InvalidAudio("audio payload is empty")
    buffer = io.BytesIO(raw)
    buffer.name = _FILENAME_BY_MIME[request.mime_type]
    return buffer


async def transcribe(request: SttIn) -> SttResult:
    """One bounded transcription call, traced to Langfuse (Hard Rule 6)."""
    settings = get_settings()
    buffer = decode_audio(request)
    kwargs: dict = {}
    if request.language_hint:  # omit entirely → Whisper auto-detect (EN/Tamil/…)
        kwargs["language"] = request.language_hint
    try:
        response = await litellm.atranscription(
            model=settings.stt_model,
            file=buffer,
            metadata={
                "generation_name": "stt.transcribe",
                "session_id": request.session_id,
                "trace_user_id": str(request.user_id) if request.user_id else None,
                "tags": [settings.stt_model],
            },
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — single provider, normalized for callers
        logger.warning("stt failed on %s: %s", settings.stt_model, exc)
        raise LLMError(f"stt failed on {settings.stt_model}: {exc}") from exc

    transcript = redact_phones((response.text or "").strip())[:4000]  # Hard Rule 8
    language = getattr(response, "language", None) or request.language_hint
    return SttResult(transcript=transcript, language=language, model=settings.stt_model)
