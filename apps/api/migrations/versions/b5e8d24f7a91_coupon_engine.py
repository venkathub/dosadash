"""coupon engine (Phase 7)

The Phase 0 schema created bare coupons/coupon_redemptions tables that were
never wired up. This adds what a real engine needs: activation state,
guardrail fields (min_subtotal, max_discount, per_user_limit), provenance
for Phase 7's AI-suggested promos, and an explicit discount column on
orders so historical totals stay auditable.

Revision ID: b5e8d24f7a91
Revises: f2c6e83a1d47
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e8d24f7a91"
down_revision: str | None = "f2c6e83a1d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    coupon_source = sa.Enum("MANUAL", "AI_SUGGESTED", name="coupon_source")
    coupon_source.create(op.get_bind(), checkfirst=True)

    op.add_column("coupons", sa.Column("description", sa.String(length=200), nullable=True))
    op.add_column("coupons", sa.Column("min_subtotal", sa.Numeric(10, 2), nullable=True))
    op.add_column("coupons", sa.Column("max_discount", sa.Numeric(10, 2), nullable=True))
    op.add_column("coupons", sa.Column("per_user_limit", sa.Integer(), nullable=True))
    op.add_column(
        "coupons",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "coupons",
        sa.Column("source", coupon_source, nullable=False, server_default="MANUAL"),
    )
    # Codes are matched case-insensitively; enforce uniqueness the same way.
    op.execute("UPDATE coupons SET code = UPPER(code)")

    op.add_column(
        "orders",
        sa.Column("discount", sa.Numeric(10, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("orders", "discount")
    op.drop_column("coupons", "source")
    op.drop_column("coupons", "is_active")
    op.drop_column("coupons", "per_user_limit")
    op.drop_column("coupons", "max_discount")
    op.drop_column("coupons", "min_subtotal")
    op.drop_column("coupons", "description")
    sa.Enum(name="coupon_source").drop(op.get_bind(), checkfirst=True)
