"""aggregator_orders: external order id mapping for the mock-aggregator channel (Phase 7)

Maps (aggregator, external_order_id) → our order, making webhook delivery
idempotent (aggregators retry) and status polling possible. Orders
themselves ride the existing orders table with channel = MOCK_AGGREGATOR
(enum value present since the Phase 0 schema).

Revision ID: e5b9d72f4a83
Revises: d8f1a63c9e25
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b9d72f4a83"
down_revision: str | None = "d8f1a63c9e25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aggregator_orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("aggregator", sa.String(length=40), nullable=False),
        sa.Column("external_order_id", sa.String(length=80), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_aggregator_orders_order_id_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_aggregator_orders")),
        sa.UniqueConstraint(
            "aggregator", "external_order_id", name=op.f("uq_aggregator_orders_aggregator")
        ),
    )


def downgrade() -> None:
    op.drop_table("aggregator_orders")
