"""Supplier invoice OCR (Phase 6): photo → VLM structured extraction.

First VLM path in the codebase. Hard Rule 1 still applies — the image goes
through litellm as a data-URL content part; vision-capable models only
(Groq's text model is skipped via an explicit chain override).
"""

import logging

from dosadash_ai.invoice.verify import verify
from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_shared import (
    INVOICE_PROMPT_VERSION,
    InvoiceExtractIn,
    InvoiceExtraction,
    InvoiceExtractResult,
)

logger = logging.getLogger(__name__)

# Vision-capable subset of the routing chain (docs/02): primary → fallback.
VISION_MODELS = ["gpt-4o-mini", "gemini/gemini-1.5-flash"]


def build_messages(request: InvoiceExtractIn) -> list[dict]:
    """System prompt + one user turn with text and the image data URL."""
    data_url = f"data:{request.mime_type};base64,{request.image_base64}"
    return [
        {"role": "system", "content": load_prompt(INVOICE_PROMPT_VERSION)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract this supplier invoice."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


async def extract_invoice(request: InvoiceExtractIn) -> InvoiceExtractResult:
    try:
        extraction, model = await structured_completion(
            messages=build_messages(request),
            response_model=InvoiceExtraction,
            trace_name="invoice_extract",
            prompt_version=INVOICE_PROMPT_VERSION,
            session_id=request.session_id,
            models=VISION_MODELS,
            max_tokens=1200,
        )
    except LLMError as exc:
        logger.warning("invoice extraction failed: %s", exc)
        return InvoiceExtractResult(error=str(exc)[:300])

    failed_checks, arithmetic_ok, confidence = verify(extraction)
    return InvoiceExtractResult(
        extraction=extraction,
        model=model,
        failed_checks=failed_checks,
        arithmetic_ok=arithmetic_ok,
        confidence=confidence,
    )
