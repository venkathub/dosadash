"""feedback_events: lifecycle sync for the self-healing loop (Phase 14 slice 1)

The loop's tail (fixer run → PR → merge → verify) previously lived only on
GitHub — status stopped at APPROVED/REJECTED and FIXED was never written.
This migration adds:
- `feedback_events`: append-only timeline (portal drill-down, Telegram
  lifecycle feed, funnel/MTTR metrics). `stage` is a String on purpose —
  the vocabulary grows with the loop and must never need a migration.
- new `feedback_status` values FIXING / PR_OPEN / VERIFIED / REOPENED.
- `feedback_reports.fix_pr_number` + `verified_at`.

Revision ID: b8e6f95a2c74
Revises: a7c3e91d5b42
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8e6f95a2c74"
down_revision: str | None = "a7c3e91d5b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in sync with dosadash_shared.FeedbackStatus (pre-Phase-14 values).
_OLD_STATUS_VALUES = (
    "RECEIVED",
    "TRACKED",
    "AUTO_FIX",
    "NEEDS_APPROVAL",
    "APPROVED",
    "REJECTED",
    "FIXED",
    "DISMISSED",
)

# New value → position (PG 16 allows ADD VALUE inside a transaction as long
# as the value is not used in the same transaction — we only add here).
_NEW_STATUS_VALUES = (
    ("FIXING", "REJECTED"),
    ("PR_OPEN", "FIXING"),
    ("VERIFIED", "FIXED"),
    ("REOPENED", "VERIFIED"),
)

# Downgrade projection: collapse Phase-14 states onto their nearest
# pre-Phase-14 ancestor so no row can hold a value the old enum lacks.
_DOWNGRADE_STATUS_MAP = {
    "FIXING": "APPROVED",
    "PR_OPEN": "APPROVED",
    "VERIFIED": "FIXED",
    "REOPENED": "FIXED",
}


def upgrade() -> None:
    for value, after in _NEW_STATUS_VALUES:
        op.execute(f"ALTER TYPE feedback_status ADD VALUE IF NOT EXISTS '{value}' AFTER '{after}'")

    op.add_column("feedback_reports", sa.Column("fix_pr_number", sa.Integer(), nullable=True))
    op.add_column("feedback_reports", sa.Column("verified_at", sa.DateTime(), nullable=True))

    op.create_table(
        "feedback_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("delivery_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["feedback_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feedback_events_report_id", "feedback_events", ["report_id"], unique=False)
    op.create_index("ix_feedback_events_stage", "feedback_events", ["stage"], unique=False)
    op.create_index(
        "ix_feedback_events_delivery_id", "feedback_events", ["delivery_id"], unique=False
    )
    op.create_index(
        "ix_feedback_events_created_at", "feedback_events", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_events_created_at", table_name="feedback_events")
    op.drop_index("ix_feedback_events_delivery_id", table_name="feedback_events")
    op.drop_index("ix_feedback_events_stage", table_name="feedback_events")
    op.drop_index("ix_feedback_events_report_id", table_name="feedback_events")
    op.drop_table("feedback_events")

    op.drop_column("feedback_reports", "verified_at")
    op.drop_column("feedback_reports", "fix_pr_number")

    # PG can't remove enum values — recreate the type without them.
    for new_value, old_value in _DOWNGRADE_STATUS_MAP.items():
        op.execute(
            f"UPDATE feedback_reports SET status = '{old_value}' WHERE status = '{new_value}'"
        )
    op.execute("ALTER TYPE feedback_status RENAME TO feedback_status_phase14")
    values_sql = ", ".join(f"'{v}'" for v in _OLD_STATUS_VALUES)
    op.execute(f"CREATE TYPE feedback_status AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE feedback_reports ALTER COLUMN status "
        "TYPE feedback_status USING status::text::feedback_status"
    )
    op.execute("DROP TYPE feedback_status_phase14")
