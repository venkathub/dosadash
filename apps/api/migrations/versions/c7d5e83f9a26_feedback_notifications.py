"""feedback_notifications: Telegram lifecycle anchors (Phase 14 slice 2)

One Telegram status card per (report, linked admin), edited in place on
every lifecycle stage — this table remembers the anchor message_id so the
api can ask the bot to edit rather than resend.

Revision ID: c7d5e83f9a26
Revises: b8e6f95a2c74
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d5e83f9a26"
down_revision: str | None = "b8e6f95a2c74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_notifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["feedback_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "tg_user_id"),
    )
    op.create_index(
        "ix_feedback_notifications_report_id", "feedback_notifications", ["report_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_notifications_report_id", table_name="feedback_notifications")
    op.drop_table("feedback_notifications")
