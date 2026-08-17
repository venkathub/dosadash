"""LLM access layer — the ONLY way this codebase talks to language models.

Hard Rule 1: all calls go through litellm (never provider SDKs directly).
Hard Rule 3: outputs are parsed into Pydantic models, never free-text.
Hard Rule 6: every call is traced to Langfuse (when keys are configured)
             with session_id and prompt version tag.

`structured_completion` walks the routing chain (gpt-4o-mini → Groq Llama
3.3 → Gemini Flash): validation failures get one in-conversation repair
attempt per model; provider errors fall through to the next model.
"""

import logging
import os

import litellm
from pydantic import BaseModel, ValidationError

from dosadash_ai.config import get_settings

logger = logging.getLogger(__name__)

_REPAIR_ATTEMPTS_PER_MODEL = 2  # initial try + one validation-repair retry


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
        for attempt in range(_REPAIR_ATTEMPTS_PER_MODEL):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=convo,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001 — provider error → next model
                logger.warning("llm call failed on %s: %s", model, exc)
                last_error = exc
                break
            raw = response.choices[0].message.content or ""
            try:
                return response_model.model_validate_json(raw), model
            except ValidationError as exc:
                logger.warning(
                    "structured output invalid on %s (attempt %d): %s", model, attempt + 1, exc
                )
                last_error = exc
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
