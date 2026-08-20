"""Promo suggestion agent (Phase 7): mining → LLM copywriting → guardrail.

The DB decides the candidates, the model writes names/copy, the guardrail
enforces the economics, and a human approves — the agent can propose but
never activate (drafts land as INACTIVE coupons / DRAFT combos api-side).
Deterministic fallback when the LLM chain fails: mined pairs get default
names and the slow-day coupon ships without prose.
"""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.promo.guardrail import (
    _default_combo,
    deterministic_coupon,
    sanitize_combos,
    sanitize_coupons,
)
from dosadash_ai.promo.mining import gather_stats, mine_pairs
from dosadash_ai.prompts import load_prompt
from dosadash_shared import (
    MAX_COMBO_SUGGESTIONS,
    PROMO_PROMPT_VERSION,
    PromoDraftBatch,
    PromoSuggestResult,
)

logger = logging.getLogger(__name__)


def build_messages(pairs: list, stats: object) -> list[dict[str, str]]:
    payload = {
        "candidate_pairs": [p.model_dump(mode="json") for p in pairs],
        "stats": stats.model_dump(mode="json"),
    }
    return [
        {"role": "system", "content": load_prompt(PROMO_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


async def suggest_promos(
    session: AsyncSession, *, session_id: str | None = None
) -> PromoSuggestResult:
    pairs = await mine_pairs(session)
    stats = await gather_stats(session)
    try:
        batch, model = await structured_completion(
            messages=build_messages(pairs, stats),
            response_model=PromoDraftBatch,
            trace_name="promo_suggest",
            prompt_version=PROMO_PROMPT_VERSION,
            session_id=session_id,
            max_tokens=900,
        )
    except LLMError as exc:
        logger.warning("promo agent LLM chain failed, deterministic fallback: %s", exc)
        return PromoSuggestResult(
            combos=[_default_combo(p) for p in pairs[:MAX_COMBO_SUGGESTIONS]],
            coupons=[deterministic_coupon(stats)],
            stats=stats,
            model=None,
            fallback=True,
        )
    return PromoSuggestResult(
        combos=sanitize_combos(batch, pairs),
        coupons=sanitize_coupons(batch, stats),
        stats=stats,
        model=model,
    )
