"""Provider Batch API access (Phase 8 slice 5) — litellm only (Hard Rule 1).

OpenAI is the only batch-capable provider in the stack, so there is no
fallback chain — the STT (Groq Whisper) and image-gen (gpt-image-1)
precedent: a batch failure leaves work undone for the next nightly run,
it never blocks or degrades anything else.

Rule 6 note: litellm's Langfuse success/failure callbacks do not fire for
the files/batches endpoints (they trace completions). Batch provenance is
therefore kept durably in the api's `review_batch_jobs` table instead —
the 50%-of-live pricing is the whole point of this path.
"""

import litellm


class BatchError(Exception):
    """The provider Batch API call failed."""


# Provider batch statuses, normalized: everything the poller needs to know
# is "still running", "done — fetch output" or "dead — record why".
_IN_PROGRESS = {"validating", "in_progress", "finalizing"}
_FAILED = {"failed", "expired", "cancelled", "cancelling"}


async def create_chat_batch(jsonl: bytes, *, completion_window: str) -> str:
    """Upload a /v1/chat/completions JSONL and start a batch. Returns the
    provider batch id."""
    try:
        batch_file = await litellm.acreate_file(
            file=("reviews.jsonl", jsonl),
            purpose="batch",
            custom_llm_provider="openai",
        )
        batch = await litellm.acreate_batch(
            completion_window=completion_window,
            endpoint="/v1/chat/completions",
            input_file_id=batch_file.id,
            custom_llm_provider="openai",
        )
    except Exception as exc:  # noqa: BLE001 — normalized for callers
        raise BatchError(f"batch submit failed: {exc}") from exc
    return batch.id


async def retrieve_chat_batch(batch_id: str) -> tuple[str, list[str] | None, str | None]:
    """→ (normalized status, output JSONL lines when completed, error).

    Status is one of "in_progress" | "completed" | "failed".
    """
    try:
        batch = await litellm.aretrieve_batch(batch_id=batch_id, custom_llm_provider="openai")
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"batch retrieve failed: {exc}") from exc

    status = str(batch.status)
    if status in _IN_PROGRESS:
        return "in_progress", None, None
    if status in _FAILED or not batch.output_file_id:
        detail = getattr(batch, "errors", None)
        return "failed", None, f"provider status {status}" + (f": {detail}" if detail else "")

    try:
        content = await litellm.afile_content(
            file_id=batch.output_file_id, custom_llm_provider="openai"
        )
    except Exception as exc:  # noqa: BLE001
        raise BatchError(f"batch output download failed: {exc}") from exc
    raw = content.text if hasattr(content, "text") else content.content.decode("utf-8")
    return "completed", [line for line in raw.splitlines() if line.strip()], None
