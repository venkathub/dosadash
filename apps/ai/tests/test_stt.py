"""STT unit + endpoint tests (Phase 7). litellm is monkeypatched — no
provider keys, no network. The real Groq Whisper path is exercised in the
prod post-deploy smoke (a live Telegram voice note)."""

import base64
from types import SimpleNamespace

import httpx
import litellm
import pytest

from dosadash_ai import config
from dosadash_ai.llm.client import LLMError
from dosadash_ai.llm.stt import InvalidAudio, decode_audio, transcribe
from dosadash_shared import SttIn

AUDIO_B64 = base64.b64encode(b"fake-ogg-opus-bytes").decode()


def _request(**overrides) -> SttIn:
    payload = {"audio_base64": AUDIO_B64, "mime_type": "audio/ogg", "session_id": "tg:1"}
    payload.update(overrides)
    return SttIn(**payload)


class FakeTranscription:
    def __init__(self, text: str, language: str | None = None) -> None:
        self.calls: list[dict] = []
        self._text = text
        self._language = language

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        response = SimpleNamespace(text=self._text)
        if self._language is not None:
            response.language = self._language
        return response


# ---------------------------------------------------------------- transcribe


async def test_transcript_is_phone_redacted(monkeypatch):
    fake = FakeTranscription("Send it to +91 98765 43210, two masala dosas please")
    monkeypatch.setattr(litellm, "atranscription", fake)
    result = await transcribe(_request())
    assert "[phone]" in result.transcript
    assert "98765" not in result.transcript
    assert "two masala dosas" in result.transcript
    assert result.model == "groq/whisper-large-v3"


async def test_language_hint_forwarded_and_autodetect_by_default(monkeypatch):
    fake = FakeTranscription("ரெண்டு மசாலா தோசை", language="ta")
    monkeypatch.setattr(litellm, "atranscription", fake)

    result = await transcribe(_request(language_hint="ta"))
    assert fake.calls[0]["language"] == "ta"
    assert result.language == "ta"

    await transcribe(_request())  # no hint → param omitted entirely (auto-detect)
    assert "language" not in fake.calls[1]


async def test_trace_metadata_carries_session_and_user(monkeypatch):
    fake = FakeTranscription("one filter coffee")
    monkeypatch.setattr(litellm, "atranscription", fake)
    await transcribe(_request(session_id="tg:777", user_id=42))
    metadata = fake.calls[0]["metadata"]
    assert metadata["session_id"] == "tg:777"
    assert metadata["trace_user_id"] == "42"


async def test_provider_failure_is_llmerror(monkeypatch):
    async def boom(**_):
        raise RuntimeError("groq down")

    monkeypatch.setattr(litellm, "atranscription", boom)
    with pytest.raises(LLMError):
        await transcribe(_request())


def test_decode_audio_rejects_garbage():
    with pytest.raises(InvalidAudio):
        decode_audio(_request(audio_base64="!!! not base64 !!!"))
    with pytest.raises(InvalidAudio):
        decode_audio(_request(audio_base64="A" * 9))  # length not a multiple of 4
    buffer = decode_audio(_request())
    assert buffer.name == "voice.ogg"  # Whisper sniffs the codec from the name
    assert buffer.read() == b"fake-ogg-opus-bytes"


# ------------------------------------------------------------------ endpoint


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_stt_requires_internal_token(ai_client):
    resp = await ai_client.post(
        "/internal/stt", json={"audio_base64": AUDIO_B64, "mime_type": "audio/ogg"}
    )
    assert resp.status_code == 403


async def test_stt_happy_path(ai_client, monkeypatch):
    fake = FakeTranscription("two masala dosas and one filter coffee")
    monkeypatch.setattr(litellm, "atranscription", fake)
    resp = await ai_client.post(
        "/internal/stt",
        json={"audio_base64": AUDIO_B64, "mime_type": "audio/ogg", "session_id": "tg:1"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transcript"] == "two masala dosas and one filter coffee"
    assert body["model"] == "groq/whisper-large-v3"


async def test_stt_bad_audio_is_422(ai_client):
    resp = await ai_client.post(
        "/internal/stt",
        json={"audio_base64": "!!! not base64 !!!", "mime_type": "audio/ogg"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422


async def test_stt_unsupported_mime_is_422(ai_client):
    resp = await ai_client.post(
        "/internal/stt",
        json={"audio_base64": AUDIO_B64, "mime_type": "audio/flac"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 422


async def test_stt_provider_failure_is_502(ai_client, monkeypatch):
    async def boom(**_):
        raise RuntimeError("groq down")

    monkeypatch.setattr(litellm, "atranscription", boom)
    resp = await ai_client.post(
        "/internal/stt",
        json={"audio_base64": AUDIO_B64, "mime_type": "audio/ogg"},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert resp.status_code == 502
