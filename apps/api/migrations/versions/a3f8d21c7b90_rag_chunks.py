"""rag_chunks: knowledge base chunks for hybrid retrieval (Phase 3)

Schema must stay in sync with dosadash_ai.rag.models.RagChunk (the AI layer
owns this table; migrations are centralized here — single DB, one history).

Revision ID: a3f8d21c7b90
Revises: e7b9c4d15a22
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3f8d21c7b90"
down_revision: str | None = "e7b9c4d15a22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_EXPRESSION = "to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(content, ''))"


def upgrade() -> None:
    op.create_table(
        "rag_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("doc_path", sa.String(length=255), nullable=False),
        sa.Column("doc_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("heading", sa.String(length=300), nullable=False),
        sa.Column("chunk_index", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(TSV_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_chunks")),
        sa.UniqueConstraint("doc_path", "chunk_index", name="uq_rag_chunks_doc_path_chunk_index"),
    )
    op.create_index(op.f("ix_rag_chunks_doc_path"), "rag_chunks", ["doc_path"], unique=False)
    op.create_index(
        "ix_rag_chunks_tsv", "rag_chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    # No ANN index on `embedding`: corpus is hundreds of chunks — exact scan
    # is fast and saves RAM on the 4 GB VPS (Hard Rule 7).


def downgrade() -> None:
    op.drop_index("ix_rag_chunks_tsv", table_name="rag_chunks")
    op.drop_index(op.f("ix_rag_chunks_doc_path"), table_name="rag_chunks")
    op.drop_table("rag_chunks")
