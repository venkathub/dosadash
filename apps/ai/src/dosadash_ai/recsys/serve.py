"""Recommendation serving (Phase 7): ALS fold-in → embedding cold-start →
DB popularity, in that order of preference.

- ALS: item factors from the exported champion + the user's LIVE order
  history (read fresh per request — Hard Rule 4 thinking: never a stale
  snapshot of taste). Matching is by canonical item name.
- Embedding cold-start: no usable history but a cart → rank orderable items
  by cosine similarity to the cart centroid, embeddings via litellm
  (Hard Rule 1) with an in-process cache flushed by the menu event cascade.
- Popularity: no history, no cart → most-ordered items from the DB (live,
  not the training snapshot), falling back to menu order on an empty DB.

Every candidate is a real, orderable menu item read from the DB this
request — the recommender cannot suggest an 86'd dish or hallucinate one
(mirrors Hard Rule 2).
"""

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.llm.client import embed_texts
from dosadash_ml.recsys.predict import RecsysChampion, load_recsys_champion, recommend_from_history
from dosadash_ml.recsys.suggest import ComboDef, SuggestCandidate, suggest_addons
from dosadash_shared import (
    CheckoutSuggestion,
    CheckoutSuggestResponse,
    RecItem,
    RecsRequest,
    RecsResponse,
    availability,
)

logger = logging.getLogger(__name__)

HISTORY_WINDOW_DAYS = 90  # same window as the order agent's "my usual"


@dataclass(frozen=True)
class RecCandidate:
    id: int
    name: str
    category: str
    price: Decimal
    is_veg: bool
    description: str | None
    orderable: bool


@lru_cache
def _champion() -> RecsysChampion | None:
    try:
        return load_recsys_champion(get_settings().model_dir)
    except Exception:  # noqa: BLE001 — missing/corrupt artifacts → non-ALS paths
        logger.warning("recsys champion unavailable — ALS path disabled", exc_info=True)
        return None


async def load_menu(session: AsyncSession) -> list[RecCandidate]:
    rows = await session.execute(
        text(
            "SELECT id, name, category, price, is_veg, description, is_available, schedule "
            "FROM menu_items ORDER BY id"
        )
    )
    return [
        RecCandidate(
            id=row.id,
            name=row.name,
            category=row.category,
            price=Decimal(row.price),
            is_veg=row.is_veg,
            description=row.description,
            orderable=row.is_available and availability.item_on_schedule(row.schedule),
        )
        for row in rows
    ]


async def load_history(session: AsyncSession, user_id: int) -> dict[str, float]:
    """{canonical item name → Σqty} over the trailing window, CANCELLED excluded."""
    rows = await session.execute(
        text(
            """
            SELECT m.name, SUM(oi.qty) AS qty
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN menu_items m ON m.id = oi.item_id
            WHERE o.user_id = :uid AND o.status != 'CANCELLED'
              AND o.placed_at >= now() - make_interval(days => :days)
            GROUP BY m.name
            """
        ),
        {"uid": user_id, "days": HISTORY_WINDOW_DAYS},
    )
    return {row.name: float(row.qty) for row in rows}


async def load_db_popularity(session: AsyncSession) -> list[int]:
    """Item ids by live order volume (trailing window) — fresher than the
    training snapshot and works even when artifacts are absent."""
    rows = await session.execute(
        text(
            """
            SELECT oi.item_id, SUM(oi.qty) AS qty
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE o.status != 'CANCELLED'
              AND o.placed_at >= now() - make_interval(days => :days)
            GROUP BY oi.item_id ORDER BY qty DESC
            """
        ),
        {"days": HISTORY_WINDOW_DAYS},
    )
    return [row.item_id for row in rows]


# ------------------------------------------------ embedding cold-start cache

_EMB_CACHE: dict[int, tuple[str, list[float]]] = {}  # item_id → (text hash, vector)


def flush_menu_embeddings() -> None:
    """Menu changed (event cascade) → cached item embeddings may be stale."""
    _EMB_CACHE.clear()


def _item_text(item: RecCandidate) -> str:
    return f"{item.name} — {item.category}. {item.description or ''}".strip()


async def _menu_embeddings(items: list[RecCandidate]) -> dict[int, np.ndarray]:
    """Embed items through litellm, reusing cached vectors when the item
    text is unchanged (cache is flushed by the menu cascade)."""
    missing = []
    for item in items:
        digest = hashlib.sha256(_item_text(item).encode()).hexdigest()
        cached = _EMB_CACHE.get(item.id)
        if cached is None or cached[0] != digest:
            missing.append((item, digest))
    if missing:
        vectors = await embed_texts([_item_text(i) for i, _ in missing], trace_name="recs.embed")
        for (item, digest), vector in zip(missing, vectors, strict=True):
            _EMB_CACHE[item.id] = (digest, vector)
    return {item.id: np.asarray(_EMB_CACHE[item.id][1]) for item in items}


def _cosine_rank(
    embeddings: dict[int, np.ndarray], cart_ids: list[int], candidates: list[RecCandidate], k: int
) -> list[tuple[RecCandidate, float]]:
    cart_vecs = [embeddings[i] for i in cart_ids if i in embeddings]
    if not cart_vecs:
        return []
    centroid = np.mean(cart_vecs, axis=0)
    centroid /= np.linalg.norm(centroid) or 1.0
    scored = []
    for item in candidates:
        vec = embeddings[item.id]
        score = float(vec @ centroid / (np.linalg.norm(vec) or 1.0))
        scored.append((item, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


# ------------------------------------------------------------------- serving


async def _ranked_candidates(
    session: AsyncSession,
    request: RecsRequest,
    candidates: list[RecCandidate],
    by_id: dict[int, RecCandidate],
) -> tuple[list[tuple[RecCandidate, float]], str, str | None]:
    """Full ranking of `candidates` (best first) + (source, model_version).
    Shared by top-k recommendations and the checkout suggester."""
    by_name = {item.name: item for item in candidates}
    cart_ids = [i for i in request.cart_item_ids if i in by_id]

    # 1) ALS on live history (returning customers)
    champion = _champion()
    if champion is not None and request.user_id is not None:
        history = await load_history(session, request.user_id)
        if history:
            ranked = recommend_from_history(
                champion,
                history,
                k=len(candidates),
                allowed=set(by_name),
                exclude=set(),
            )
            if ranked:
                pairs = [(by_name[n], s) for n, s in ranked if n in by_name]
                return pairs, "als", champion.version

    # 2) Embedding similarity to the cart (cold-start with context)
    if cart_ids:
        try:
            embeddings = await _menu_embeddings([*candidates, *[by_id[i] for i in cart_ids]])
            pairs = _cosine_rank(embeddings, cart_ids, candidates, len(candidates))
            if pairs:
                return pairs, "embedding", None
        except Exception:  # noqa: BLE001 — embedding provider down → popularity
            logger.warning("embedding cold-start failed, using popularity", exc_info=True)

    # 3) Popularity (cold-start without context)
    popular_ids = await load_db_popularity(session)
    ordered = [by_id[i] for i in popular_ids if i in by_id and by_id[i] in candidates]
    ordered += [c for c in candidates if c not in ordered]  # empty-DB fallback: menu order
    pairs = [(item, float(len(ordered) - rank)) for rank, item in enumerate(ordered)]
    return pairs, "popular", None


async def recommend(session: AsyncSession, request: RecsRequest) -> RecsResponse:
    menu = await load_menu(session)
    by_id = {item.id: item for item in menu}
    cart_ids = {i for i in request.cart_item_ids if i in by_id}
    candidates = [i for i in menu if i.orderable and i.id not in cart_ids]
    if not candidates:
        return RecsResponse(items=[], source="popular", model_version=None)
    pairs, source, model_version = await _ranked_candidates(session, request, candidates, by_id)
    items = [_rec_item(item, score) for item, score in pairs[: request.k]]
    return RecsResponse(items=items, source=source, model_version=model_version)


def _rec_item(item: RecCandidate, score: float) -> RecItem:
    return RecItem(
        item_id=item.id, name=item.name, price=item.price, is_veg=item.is_veg, score=round(score, 4)
    )


# ------------------------------------------------------- checkout suggester


async def load_approved_combos(session: AsyncSession) -> list[tuple[str, list[int]]]:
    """(name, item_ids) of APPROVED combos — the only ones customers see."""
    rows = await session.execute(
        text("SELECT name, item_ids FROM combos WHERE status = 'APPROVED' ORDER BY id")
    )
    return [(row.name, list(row.item_ids)) for row in rows]


async def suggest_checkout(session: AsyncSession, request: RecsRequest) -> CheckoutSuggestResponse:
    """Deterministic combo/pairing suggestions for the checkout footer,
    ranked by the same ALS/embedding/popularity chain as /internal/recs.
    The rule engine lives in dosadash_ml.recsys.suggest — the identical code
    the synthetic A/B sim measures (attach 15.6% vs random 12.8%)."""
    menu = await load_menu(session)
    by_id = {item.id: item for item in menu}
    cart = [by_id[i] for i in request.cart_item_ids if i in by_id]
    if not cart:
        return CheckoutSuggestResponse(suggestions=[], source="popular", model_version=None)
    cart_ids = {item.id for item in cart}
    candidates = [i for i in menu if i.orderable and i.id not in cart_ids]
    if not candidates:
        return CheckoutSuggestResponse(suggestions=[], source="popular", model_version=None)

    pairs, source, model_version = await _ranked_candidates(session, request, candidates, by_id)
    scores = {item.name: score for item, score in pairs}
    combo_defs = [
        ComboDef(name=name, item_names=tuple(by_id[i].name for i in ids if i in by_id))
        for name, ids in await load_approved_combos(session)
    ]
    suggestions = suggest_addons(
        cart_names={item.name for item in cart},
        cart_categories={item.category for item in cart},
        candidates=[
            SuggestCandidate(name=c.name, category=c.category, score=scores.get(c.name, -1e9))
            for c in candidates
        ],
        combos=combo_defs,
        max_suggestions=min(request.k, 4),  # api sends k≤4; keep the footer tight
    )
    by_name = {c.name: c for c in candidates}
    return CheckoutSuggestResponse(
        suggestions=[
            CheckoutSuggestion(
                item_id=by_name[s.item_name].id,
                name=s.item_name,
                price=by_name[s.item_name].price,
                is_veg=by_name[s.item_name].is_veg,
                kind=s.kind,
                reason=s.reason,
            )
            for s in suggestions
        ],
        source=source,
        model_version=model_version,
    )
