"""review_batch_jobs: provider Batch API job tracking (Phase 8 slice 5)

Nightly bulk review scoring: the local INT8 champion scores what it is
confident about at ₹0; the residue is submitted to the OpenAI Batch API at
50% of live pricing. This table is the durable job state the hourly poller
walks — `chunks` records the custom_id → review_ids mapping so the ai
service stays stateless, and SUBMITTED rows double as the dedup set (a
review already in flight is never re-submitted).

Revision ID: c8e5a97d3f62
Revises: b6f4a92c7d18
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c8e5a97d3f62"
down_revision: str | None = "b6f4a92c7d18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_batch_jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_batch_id", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("chunks", JSONB(), nullable=False),
        sa.Column("n_reviews", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("SUBMITTED", "COMPLETED", "FAILED", name="review_batch_status"),
            nullable=False,
        ),
        sa.Column("scored", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_batch_id"),
    )
    op.create_index("ix_review_batch_jobs_status", "review_batch_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_review_batch_jobs_status", table_name="review_batch_jobs")
    op.drop_table("review_batch_jobs")
    sa.Enum(name="review_batch_status").drop(op.get_bind(), checkfirst=True)
