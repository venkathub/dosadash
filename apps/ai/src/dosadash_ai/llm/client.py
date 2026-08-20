"""LLM access layer — the ONLY way this codebase talks to language models.

Hard Rule 1: all calls go through litellm (never provider SDKs directly).
Hard Rule 3: outputs are parsed into Pydantic models, never free-text.
Hard Rule 6: every call is traced to Langfuse (when keys are configured)
             with session_id and prompt version tag.

`structured_completion` walks the routing chain (gpt-4o-mini → Groq Llama
3.3 → Gemini Flash): validation failures get one in-conversation repair
attempt per model; provider errors fall through to the next model.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator

import litellm
from pydantic import BaseModel, ValidationError

from dosadash_ai.config import get_settings
from dosadash_ai.llm.usage_stats import get_usage_stats

logger = logging.getLogger(__name__)

_REPAIR_ATTEMPTS_PER_MODEL = 2  # initial try + one validation-repair retry
_RATE_LIMIT_RETRIES_PER_MODEL = 2  # brief same-model retries on 429 before falling through
_RATE_LIMIT_BACKOFF_SECONDS = 2.0


class LLMError(Exception):
    """All models in the routing chain failed."""


def configure_tracing() -> None:
    """Enable the Langfuse callback when keys are present (no-op otherwise)."""
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        litellm.success_callback = ["langfuse"]
        litellm.failure_callback = ["langfuse"]
        logger.info("Langfuse tracing enabled for litellm")


async def structured_completion[T: BaseModel](
    *,
    messages: list[dict[str, str]],
    response_model: type[T],
    trace_name: str,
    prompt_version: str,
    session_id: str | None = None,
    user_id: str | None = None,
    models: list[str] | None = None,
    temperature: float = 0.0,
    max_tokens: int = 700,
) -> tuple[T, str]:
    """Run the chain until one model returns JSON that validates against
    `response_model`. Returns (parsed, model_used)."""
    chain = models or get_settings().llm_models
    metadata = {
        "generation_name": trace_name,
        "session_id": session_id,
        "trace_user_id": user_id,
        "tags": [prompt_version],
    }
    last_error: Exception | None = None

    for model in chain:
        convo = list(messages)
        rate_limit_budget = _RATE_LIMIT_RETRIES_PER_MODEL
        attempt = 0
        while attempt < _REPAIR_ATTEMPTS_PER_MODEL:
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=convo,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    metadata=metadata,
                )
            except litellm.RateLimitError as exc:
                # 429s are usually seconds-long (TPM windows) — a brief same-model
                # retry beats falling through the chain (caught by the live eval
                # gate: a 150-case run brushed OpenAI's TPM ceiling mid-run).
                if rate_limit_budget > 0:
                    rate_limit_budget -= 1
                    logger.info("rate limited on %s, retrying same model shortly", model)
                    await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                    continue  # same attempt, same model
                logger.warning("rate limit persists on %s, falling through", model)
                last_error = exc
                break
            except Exception as exc:  # noqa: BLE001 — provider error → next model
                logger.warning("llm call failed on %s: %s", model, exc)
                last_error = exc
                break
            # Phase 9 observability: accumulate prompt-cache token counters
            # (best-effort, never raises). Streaming path is not counted —
            # providers omit usage on streamed chunks; Langfuse stays the
            # billing source of truth.
            await get_usage_stats().record_response(response)
            raw = response.choices[0].message.content or ""
            try:
                return response_model.model_validate_json(raw), model
            except ValidationError as exc:
                logger.warning(
                    "structured output invalid on %s (attempt %d): %s", model, attempt + 1, exc
                )
                last_error = exc
                attempt += 1
                convo = [
                    *convo,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "Your JSON failed schema validation: "
                            f"{exc.errors()[:3]}. Respond again with ONLY the "
                            "corrected JSON object, no prose."
                        ),
                    },
                ]

    raise LLMError(f"all models failed for {trace_name}: {last_error}") from last_error


async def stream_text_completion(
    *,
    messages: list[dict[str, str]],
    trace_name: str,
    prompt_version: str,
    session_id: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 900,
) -> AsyncIterator[str]:
    """Stream raw JSON-mode content pieces from ONE model (the chain's
    primary by default). No fallback here: streaming callers catch failures
    and fall back to `structured_completion` (which walks the whole chain).
    Any provider error surfaces as LLMError.
    """
    chosen = model or get_settings().llm_models[0]
    metadata = {
        "generation_name": trace_name,
        "session_id": session_id,
        "trace_user_id": user_id,
        "tags": [prompt_version],
    }
    try:
        response = await litellm.acompletion(
            model=chosen,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            stream=True,
            metadata=metadata,
        )
        async for chunk in response:
            piece = chunk.choices[0].delta.content
            if piece:
                yield piece
    except Exception as exc:  # noqa: BLE001 — normalized for callers
        raise LLMError(f"stream failed on {chosen}: {exc}") from exc


async def embed_texts(
    texts: list[str],
    *,
    trace_name: str = "rag.embed",
) -> list[list[float]]:
    """Embed texts via litellm (Hard Rule 1) in input order.

    Callers must redact PII first (Hard Rule 8) — embeddings are provider
    calls like any other.
    """
    if not texts:
        return []
    response = await litellm.aembedding(
        model=get_settings().embedding_model,
        input=texts,
        metadata={"generation_name": trace_name},
    )
    data = sorted(response.data, key=lambda d: d["index"])
    if len(data) != len(texts):
        raise LLMError(f"embedding count mismatch: {len(data)} != {len(texts)}")
    return [d["embedding"] for d in data]
