"""reviews: customer reviews + aspect-sentiment scoring + owner replies (Phase 8)

One review per DELIVERED order (unique order_id). The customer writes only
rating + text; `sentiment`/`aspects` are filled by the scoring path
(zero-shot LLM first, quantized LoRA later) with model/prompt provenance so
the fine-tune-vs-API benchmark is auditable per row. `reply_draft` holds the
AI-drafted owner reply (backoffice-only); publishing copies into
`owner_reply` with `reply_source` AI_DRAFT|MANUAL (docs/06 shape).

Synthetic planted labels from datagen are deliberately NOT stored — the DB
only ever holds what a real system would have.

Revision ID: b6f4a92c7d18
Revises: e5b9d72f4a83
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b6f4a92c7d18"
down_revision: str | None = "e5b9d72f4a83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "sentiment",
            sa.Enum("POSITIVE", "NEGATIVE", "MIXED", name="review_sentiment"),
            nullable=True,
        ),
        sa.Column("aspects", JSONB(), nullable=True),
        sa.Column("scored_model", sa.String(length=80), nullable=True),
        sa.Column("scored_prompt_version", sa.String(length=40), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_draft", sa.Text(), nullable=True),
        sa.Column("reply_draft_model", sa.String(length=80), nullable=True),
        sa.Column("owner_reply", sa.Text(), nullable=True),
        sa.Column(
            "reply_source", sa.Enum("AI_DRAFT", "MANUAL", name="reply_source"), nullable=True
        ),
        sa.Column("replied_by", sa.BigInteger(), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_reviews_rating_range")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_reviews_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_reviews_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replied_by"], ["users.id"], name=op.f("fk_reviews_replied_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("order_id", name=op.f("uq_reviews_order_id")),
    )
    op.create_index(op.f("ix_reviews_user_id"), "reviews", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_reviews_user_id"), table_name="reviews")
    op.drop_table("reviews")
    sa.Enum(name="review_sentiment").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="reply_source").drop(op.get_bind(), checkfirst=True)
