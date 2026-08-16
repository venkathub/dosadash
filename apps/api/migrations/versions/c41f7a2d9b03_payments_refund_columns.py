"""payments: provider_payment_id + refund_id (Phase 2 admin refund flow)

Revision ID: c41f7a2d9b03
Revises: 95b59b942b55
Create Date: 2026-08-17

`provider_payment_id` is the captured payment's gateway id (needed to call
the provider refund API); `refund_id` records the resulting refund.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41f7a2d9b03"
down_revision: str | None = "95b59b942b55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payments", sa.Column("provider_payment_id", sa.String(length=120), nullable=True)
    )
    op.add_column("payments", sa.Column("refund_id", sa.String(length=120), nullable=True))
    op.create_index(
        op.f("ix_payments_provider_payment_id"), "payments", ["provider_payment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_provider_payment_id"), table_name="payments")
    op.drop_column("payments", "refund_id")
    op.drop_column("payments", "provider_payment_id")
