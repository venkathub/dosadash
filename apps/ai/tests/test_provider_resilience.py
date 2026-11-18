"""Provider outage and budget failures must not bypass routing protections."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from dosadash_ai.llm import client
from dosadash_ai.llm.budget import ProviderBudgetExceeded


class Answer(BaseModel):
    answer: str


async def test_primary_outage_uses_fallback_and_counts_both_attempts(monkeypatch):
    reserved = []
    called = []

    async def reserve(operation):
        reserved.append(operation)

    async def call(**kw):
        called.append(kw["model"])
        assert kw["num_retries"] == kw["max_retries"] == 0 and kw["timeout"] <= 120
        if len(called) == 1:
            raise TimeoutError("synthetic outage")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))]
        )

    monkeypatch.setattr(client, "reserve", reserve)
    monkeypatch.setattr(client.litellm, "acompletion", call)
    result, model = await client.structured_completion(
        messages=[],
        response_model=Answer,
        trace_name="test",
        prompt_version="test",
        models=["primary", "fallback"],
    )
    assert model == "fallback" and result.answer == "ok" and reserved == ["chat", "chat"]


async def test_budget_rejection_never_reaches_any_provider(monkeypatch):
    async def denied(operation):
        raise ProviderBudgetExceeded("exhausted")

    async def forbidden(**kw):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(client, "reserve", denied)
    monkeypatch.setattr(client.litellm, "acompletion", forbidden)
    with pytest.raises(ProviderBudgetExceeded):
        await client.structured_completion(
            messages=[],
            response_model=Answer,
            trace_name="test",
            prompt_version="test",
            models=["primary", "fallback"],
        )
    with pytest.raises(ProviderBudgetExceeded):
        async for _ in client.stream_text_completion(
            messages=[], trace_name="test", prompt_version="test"
        ):
            pass
    with pytest.raises(ProviderBudgetExceeded):
        await client.embed_texts(["test"])


async def test_hung_provider_is_bounded(monkeypatch):
    import asyncio

    from dosadash_ai.config import get_settings

    monkeypatch.setattr(get_settings(), "provider_timeout_seconds", 0.01)

    async def hang(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(client.litellm, "acompletion", hang)
    with pytest.raises(client.LLMError):
        await asyncio.wait_for(
            client.structured_completion(
                messages=[],
                response_model=Answer,
                trace_name="test",
                prompt_version="test",
                models=["primary"],
            ),
            timeout=0.5,
        )


async def test_stream_timeout_closes_response_and_retains_reservation(monkeypatch):
    import asyncio

    from dosadash_ai.config import get_settings

    monkeypatch.setattr(get_settings(), "provider_timeout_seconds", 0.01)
    closed = []

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10)

        async def aclose(self):
            closed.append(True)

    async def completion(**kwargs):
        return Stream()

    monkeypatch.setattr(client.litellm, "acompletion", completion)
    with pytest.raises(client.LLMError):
        async for _ in client.stream_text_completion(
            messages=[], trace_name="test", prompt_version="test"
        ):
            pass
    assert closed == [True]


async def test_other_paid_operations_reject_before_provider_io(monkeypatch):
    from dosadash_ai.llm import batch, stt
    from dosadash_ai.routers import imagegen
    from dosadash_shared import MenuImageRequest, SttIn

    async def denied(*args, **kwargs):
        raise ProviderBudgetExceeded("exhausted")

    async def forbidden(**kwargs):
        raise AssertionError("provider must not be called")

    for module in (batch, stt, imagegen):
        monkeypatch.setattr(module, "reserve", denied)
    for name in ("atranscription", "aimage_generation", "acreate_file", "acreate_batch"):
        monkeypatch.setattr(client.litellm, name, forbidden)
    with pytest.raises(ProviderBudgetExceeded):
        await stt.transcribe(SttIn(audio_base64="aGVsbG8=", mime_type="audio/wav"))
    with pytest.raises(ProviderBudgetExceeded):
        await imagegen.generate_image(
            MenuImageRequest(item_name="Dosa", category="Dosa", is_veg=True)
        )
    with pytest.raises(batch.BatchError, match="exhausted"):
        await batch.create_chat_batch(b"{}\n", completion_window="24h")
