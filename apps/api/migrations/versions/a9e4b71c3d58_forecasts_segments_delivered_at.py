"""forecasts + customer_segments + orders.delivered_at (Phase 5 ML)

- `orders.delivered_at`: actual delivery timestamp — the ETA-regression
  label. Backfilled for synthetic history by the seeder; live orders get it
  from the order state machine on DELIVERED.
- `forecasts`: per-dish daily demand predictions (nightly Celery scoring from
  the MLflow champion model), unique per (item_id, date).
- `customer_segments`: nightly RFM/churn/LTV scoring output, one row per user.

Revision ID: a9e4b71c3d58
Revises: f8c2d94a1b37
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9e4b71c3d58"
down_revision: str | None = "f8c2d94a1b37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "forecasts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "item_id",
            sa.BigInteger(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("predicted_qty", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("item_id", "date", name="uq_forecasts_item_id_date"),
    )
    op.create_index("ix_forecasts_item_id", "forecasts", ["item_id"])
    op.create_index("ix_forecasts_date", "forecasts", ["date"])

    op.create_table(
        "customer_segments",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rfm_tier", sa.String(length=20), nullable=False),
        sa.Column("churn_risk", sa.Float(), nullable=False),
        sa.Column("ltv", sa.Numeric(12, 2), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customer_segments_rfm_tier", "customer_segments", ["rfm_tier"])


def downgrade() -> None:
    op.drop_index("ix_customer_segments_rfm_tier", table_name="customer_segments")
    op.drop_table("customer_segments")
    op.drop_index("ix_forecasts_date", table_name="forecasts")
    op.drop_index("ix_forecasts_item_id", table_name="forecasts")
    op.drop_table("forecasts")
    op.drop_column("orders", "delivered_at")
