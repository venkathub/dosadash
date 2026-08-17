"""SQLAlchemy model for rag_chunks — owned by the AI layer.

Production DDL lives in the api service's alembic migrations (single shared
DB, one migration history); this model MUST stay in sync with the
`rag_chunks` migration. Tests create tables from this metadata.
"""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Computed, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dosadash_shared import EMBEDDING_DIM

# Must match the migration exactly (English config; heading weighted into tsv).
TSV_EXPRESSION = "to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(content, ''))"


class RagBase(DeclarativeBase):
    pass


class RagChunk(RagBase):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_path: Mapped[str] = mapped_column(String(255), index=True)  # e.g. "faq.md"
    doc_type: Mapped[str] = mapped_column(String(30))  # front-matter doc_type
    title: Mapped[str] = mapped_column(String(200))  # front-matter title
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    heading: Mapped[str] = mapped_column(String(300))  # "H2 › H3" breadcrumb
    chunk_index: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 → skip re-embed
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("doc_path", "chunk_index", name="uq_rag_chunks_doc_path_chunk_index"),
        Index("ix_rag_chunks_tsv", "tsv", postgresql_using="gin"),
        # No ANN index on `embedding`: the corpus is small (hundreds of chunks)
        # and exact scan is fast; saves RAM on the 4 GB VPS (Hard Rule 7).
    )
