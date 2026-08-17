"""Knowledge ingestion: chunk → diff by content_hash → embed → upsert.

Idempotent and incremental: unchanged chunks (same doc_path/heading/content
hash) are never re-embedded; removed docs/sections are deleted. Reused by
the ingestion CLI and (Task 3) the menu-edit / knowledge-change re-embed
cascade.

Usage:
    uv run python -m dosadash_ai.rag.ingest [--knowledge-dir PATH] [--dry-run]
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.client import embed_texts
from dosadash_ai.rag.chunking import Chunk, load_knowledge_dir
from dosadash_ai.rag.models import RagChunk

logger = logging.getLogger(__name__)

_EMBED_BATCH = 64


@dataclass(frozen=True)
class IngestReport:
    total_chunks: int
    embedded: int  # new or changed → embedded this run
    unchanged: int
    deleted: int

    def __str__(self) -> str:
        return (
            f"ingest: {self.total_chunks} chunks — {self.embedded} embedded, "
            f"{self.unchanged} unchanged, {self.deleted} deleted"
        )


async def ingest_chunks(
    session: AsyncSession, chunks: list[Chunk], *, dry_run: bool = False
) -> IngestReport:
    """Sync the rag_chunks table to `chunks` (the full desired corpus)."""
    existing = {
        (row.doc_path, row.chunk_index): row for row in (await session.scalars(select(RagChunk)))
    }
    desired_keys = {(c.doc_path, c.chunk_index) for c in chunks}

    to_embed = [
        c
        for c in chunks
        if (c.doc_path, c.chunk_index) not in existing
        or existing[(c.doc_path, c.chunk_index)].content_hash != c.content_hash
    ]
    stale = [key for key in existing if key not in desired_keys]
    report = IngestReport(
        total_chunks=len(chunks),
        embedded=len(to_embed),
        unchanged=len(chunks) - len(to_embed),
        deleted=len(stale),
    )
    if dry_run:
        return report

    embeddings: list[list[float]] = []
    for start in range(0, len(to_embed), _EMBED_BATCH):
        batch = to_embed[start : start + _EMBED_BATCH]
        embeddings.extend(await embed_texts([f"{c.heading}\n{c.content}" for c in batch]))

    for chunk, embedding in zip(to_embed, embeddings, strict=True):
        row = existing.get((chunk.doc_path, chunk.chunk_index))
        if row is None:
            row = RagChunk(doc_path=chunk.doc_path, chunk_index=chunk.chunk_index)
            session.add(row)
        row.doc_type = chunk.doc_type
        row.title = chunk.title
        row.tags = list(chunk.tags)
        row.heading = chunk.heading
        row.content = chunk.content
        row.content_hash = chunk.content_hash
        row.embedding = embedding

    for doc_path, chunk_index in stale:
        await session.execute(
            delete(RagChunk).where(
                RagChunk.doc_path == doc_path, RagChunk.chunk_index == chunk_index
            )
        )
    await session.commit()
    return report


async def ingest_knowledge_dir(
    session: AsyncSession, knowledge_dir: Path | None = None, *, dry_run: bool = False
) -> IngestReport:
    directory = knowledge_dir or Path(get_settings().knowledge_dir)
    chunks = load_knowledge_dir(directory)
    report = await ingest_chunks(session, chunks, dry_run=dry_run)
    if not dry_run and (report.embedded or report.deleted):
        # Knowledge changed → cached answers may cite stale content
        # (Hard Rule 4: the AI layer never drifts from source state).
        from dosadash_ai.llm.semcache import get_semcache

        await get_semcache().flush()
    return report


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge/ into rag_chunks")
    parser.add_argument("--knowledge-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    async with get_sessionmaker()() as session:
        report = await ingest_knowledge_dir(
            session, knowledge_dir=args.knowledge_dir, dry_run=args.dry_run
        )
    print(report)


if __name__ == "__main__":
    asyncio.run(_main())
