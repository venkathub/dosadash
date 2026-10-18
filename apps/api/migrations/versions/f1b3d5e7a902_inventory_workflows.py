"""Inventory checkpoints and immutable forecast health evidence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f1b3d5e7a902"
down_revision = "e9a2c6d4f801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_observations",
        sa.Column(
            "item_id",
            sa.BigInteger(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("issued_day", sa.Date(), primary_key=True),
        sa.Column("predicted_qty", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(60), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_forecast_observations_date", "forecast_observations", ["date"])
    op.create_table(
        "forecast_health",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("report", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "inventory_workflows",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("coverage_days", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("checkpoint", JSONB()),
        sa.Column("po_ids", JSONB(), nullable=False),
        sa.Column("skipped", JSONB(), nullable=False),
        sa.Column("error", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inventory_workflows_stage", "inventory_workflows", ["stage"])


def downgrade() -> None:
    op.drop_table("inventory_workflows")
    op.drop_table("forecast_health")
    op.drop_table("forecast_observations")
