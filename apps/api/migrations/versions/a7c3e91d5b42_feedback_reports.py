"""feedback_reports: self-healing loop intake (Phase 13 slice 1, docs/14)

GUI bug/feature reports land here first (phone-redacted — Hard Rule 8),
then mirror to GitHub issues best-effort. GitHub labels drive the fixer
automation; `status` is the local projection, `triage` JSONB carries the
Slice-3 LLM triage provenance, and `dedupe_hash` collapses repeat reports
onto the open original so report spam can never flood the issue tracker.

Revision ID: a7c3e91d5b42
Revises: f4a7b62c8d19
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a7c3e91d5b42"
down_revision: str | None = "f4a7b62c8d19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "reporter_tier",
            sa.Enum("ANON", "CUSTOMER", "STAFF", name="feedback_reporter_tier"),
            nullable=False,
        ),
        sa.Column("type", sa.Enum("BUG", "FEATURE", name="feedback_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "TRACKED",
                "AUTO_FIX",
                "NEEDS_APPROVAL",
                "APPROVED",
                "REJECTED",
                "FIXED",
                "DISMISSED",
                name="feedback_status",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context", JSONB(), nullable=True),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("github_issue_number", sa.Integer(), nullable=True),
        sa.Column("github_error", sa.String(length=300), nullable=True),
        sa.Column("triage", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_reports_user_id", "feedback_reports", ["user_id"], unique=False)
    op.create_index("ix_feedback_reports_status", "feedback_reports", ["status"], unique=False)
    op.create_index(
        "ix_feedback_reports_dedupe_hash", "feedback_reports", ["dedupe_hash"], unique=False
    )
    op.create_index(
        "ix_feedback_reports_github_issue_number",
        "feedback_reports",
        ["github_issue_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_reports_github_issue_number", table_name="feedback_reports")
    op.drop_index("ix_feedback_reports_dedupe_hash", table_name="feedback_reports")
    op.drop_index("ix_feedback_reports_status", table_name="feedback_reports")
    op.drop_index("ix_feedback_reports_user_id", table_name="feedback_reports")
    op.drop_table("feedback_reports")
    sa.Enum(name="feedback_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="feedback_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="feedback_reporter_tier").drop(op.get_bind(), checkfirst=True)
