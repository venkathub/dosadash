"""Public recommendations endpoint (Phase 7): "You might like" on the
customer menu page.

Anonymous-friendly (like chat): a JWT personalizes via order history, no
JWT means cold-start (cart embedding similarity or popularity). The api
NEVER fails the menu page over recommendations — ai-service errors degrade
to an empty list with source="unavailable".
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dosadash_api.routers.chat import OptionalUser
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import RecsRequest, RecsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/recs", tags=["recs"])

_MAX_CART_IDS = 20


def _parse_cart(cart: str) -> list[int]:
    """Comma-separated ids; junk entries are dropped, count is bounded."""
    ids = []
    for chunk in cart.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return ids[:_MAX_CART_IDS]


@router.get("", response_model=RecsResponse)
async def recommendations(
    user: OptionalUser,
    client: Annotated[AIClient, Depends(get_ai_client)],
    cart: str = "",
    k: Annotated[int, Query(ge=1, le=12)] = 6,
) -> RecsResponse:
    request = RecsRequest(
        user_id=user.id if user else None,
        cart_item_ids=_parse_cart(cart),
        k=k,
    )
    try:
        return await client.recommend(request)
    except AIServiceError:
        logger.warning("recs unavailable — returning empty strip")
        return RecsResponse(items=[], source="unavailable", model_version=None)
