"""Durable provider admission budget."""

import sqlalchemy as sa
from alembic import op

revision = "a2c4e6f80913"
down_revision = "f1b3d5e7a902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_budget_days",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("charged_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("known_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("unknown_calls", sa.Integer(), nullable=False),
    )
    op.create_table(
        "provider_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("day", sa.Date(), sa.ForeignKey("provider_budget_days.day"), nullable=False),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("actual_usd", sa.Numeric(18, 8)),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("settled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_provider_reservations_day", "provider_reservations", ["day"])


def downgrade() -> None:
    op.drop_table("provider_reservations")
    op.drop_table("provider_budget_days")
