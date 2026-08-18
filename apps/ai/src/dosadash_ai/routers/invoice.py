"""Internal invoice endpoint (Phase 6 — apps/ai reasons, apps/api owns the
review queue and stock mutations).

POST /internal/invoice/extract — X-Internal-Token guarded.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from dosadash_ai.config import get_settings
from dosadash_ai.invoice.extract import extract_invoice
from dosadash_shared import InvoiceExtractIn, InvoiceExtractResult

router = APIRouter(prefix="/internal/invoice", tags=["internal:invoice"])


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/extract", response_model=InvoiceExtractResult)
async def extract(
    request: InvoiceExtractIn,
    x_internal_token: Annotated[str, Header()] = "",
) -> InvoiceExtractResult:
    _check_internal_token(x_internal_token)
    return await extract_invoice(request)
