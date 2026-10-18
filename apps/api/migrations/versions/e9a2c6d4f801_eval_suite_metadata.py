"""Preserve live suite outcomes/provenance; historical reports retain empty objects.

Revision ID: e9a2c6d4f801
Revises: b9f4e57a2c86
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e9a2c6d4f801"
down_revision = "b9f4e57a2c86"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("suite_results", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "eval_runs",
        sa.Column("run_metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("eval_runs", "run_metadata")
    op.drop_column("eval_runs", "suite_results")
