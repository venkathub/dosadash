"""Hybrid retrieval: BM25-style FTS + vector cosine, fused with RRF.

Two cheap indexed queries (GIN tsvector + exact vector scan over a small
corpus), fused with Reciprocal Rank Fusion — no tuning weights to maintain,
robust when one signal is silent (e.g. Tanglish queries rarely hit English
FTS but embed fine). LLM rerank + citations layer on top in the answers
endpoint (Task 3).
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.rag.models import RagChunk

RRF_K = 60  # standard damping constant
_CANDIDATES_EACH = 12  # per-signal candidate pool


@dataclass(frozen=True)
class ScoredChunk:
    chunk: RagChunk
    score: float


def rrf_fuse(rankings: list[list[int]], *, k: int = RRF_K) -> dict[int, float]:
    """Fuse ranked id lists: score(id) = Σ 1 / (k + rank). Rank is 1-based."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


async def hybrid_search(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    *,
    top_k: int = 6,
) -> list[ScoredChunk]:
    """Retrieve top_k chunks by RRF over FTS and vector rankings."""
    fts_ids = list(
        await session.scalars(
            select(RagChunk.id)
            .where(RagChunk.tsv.op("@@")(func.websearch_to_tsquery("english", query)))
            .order_by(
                func.ts_rank(RagChunk.tsv, func.websearch_to_tsquery("english", query)).desc()
            )
            .limit(_CANDIDATES_EACH)
        )
    )
    vec_ids = list(
        await session.scalars(
            select(RagChunk.id)
            .where(RagChunk.embedding.is_not(None))
            .order_by(RagChunk.embedding.cosine_distance(query_embedding))
            .limit(_CANDIDATES_EACH)
        )
    )

    scores = rrf_fuse([fts_ids, vec_ids])
    top_ids = sorted(scores, key=lambda i: scores[i], reverse=True)[:top_k]
    if not top_ids:
        return []
    rows = {
        row.id: row
        for row in await session.scalars(select(RagChunk).where(RagChunk.id.in_(top_ids)))
    }
    return [ScoredChunk(chunk=rows[i], score=scores[i]) for i in top_ids if i in rows]
