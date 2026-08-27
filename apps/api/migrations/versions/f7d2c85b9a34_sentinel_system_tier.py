"""sentinel SYSTEM reporter tier (Phase 15 slice 1, docs/15)

The production sentinel files feedback reports itself (telemetry as a
reporter). Those rows carry `reporter_tier = 'SYSTEM'` so triage policy,
metrics, and the portal can distinguish machine-filed incidents from human
reports. Enum-value addition only — PG 16 allows ADD VALUE inside a
transaction as long as the value is not used in the same transaction.

Revision ID: f7d2c85b9a34
Revises: d9e6f24a8b35
Create Date: 2026-08-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7d2c85b9a34"
down_revision: str | None = "d9e6f24a8b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in sync with dosadash_shared.ReporterTier (pre-Phase-15 values).
_OLD_TIER_VALUES = ("ANON", "CUSTOMER", "STAFF")


def upgrade() -> None:
    op.execute("ALTER TYPE feedback_reporter_tier ADD VALUE IF NOT EXISTS 'SYSTEM' AFTER 'STAFF'")


def downgrade() -> None:
    # PG can't remove enum values — recreate the type without SYSTEM.
    # Sentinel rows collapse onto STAFF (nearest trusted tier; the row's
    # user_id stays NULL so the origin is still recognizable).
    op.execute("UPDATE feedback_reports SET reporter_tier = 'STAFF' WHERE reporter_tier = 'SYSTEM'")
    op.execute("ALTER TYPE feedback_reporter_tier RENAME TO feedback_reporter_tier_phase15")
    values_sql = ", ".join(f"'{v}'" for v in _OLD_TIER_VALUES)
    op.execute(f"CREATE TYPE feedback_reporter_tier AS ENUM ({values_sql})")
    op.execute(
        "ALTER TABLE feedback_reports ALTER COLUMN reporter_tier "
        "TYPE feedback_reporter_tier USING reporter_tier::text::feedback_reporter_tier"
    )
    op.execute("DROP TYPE feedback_reporter_tier_phase15")
