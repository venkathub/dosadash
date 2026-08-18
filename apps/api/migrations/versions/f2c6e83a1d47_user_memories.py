"""user_memories — long-term agent memory (Phase 6)

Episodic store beyond session checkpoints: order episodes written by
order_service on every placed order; the order agent's context loader reads
the latest few (plus a derived "usual") for logged-in customers.

Revision ID: f2c6e83a1d47
Revises: e8a5d47b9c31
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f2c6e83a1d47"
down_revision: str | None = "e8a5d47b9c31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="EPISODE"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_user_memories_user_id_at", "user_memories", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_user_memories_user_id_at", table_name="user_memories")
    op.drop_table("user_memories")
