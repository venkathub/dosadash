"""escalations — support-agent inbox (Phase 6)

Refund requests and anything the support agent cannot (or must not) resolve
land here for a human. Resolution may trigger the real provider refund via
order_service (admin/owner only) — the agent itself never moves money.

Revision ID: e8a5d47b9c31
Revises: d7f3a91c5e24
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8a5d47b9c31"
down_revision: str | None = "d7f3a91c5e24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

escalation_status = sa.Enum("OPEN", "RESOLVED", "DISMISSED", name="escalation_status")


def upgrade() -> None:
    op.create_table(
        "escalations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", escalation_status, nullable=False, server_default="OPEN"),
        sa.Column("customer_message", sa.Text(), nullable=False),
        sa.Column("agent_summary", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolution_note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_escalations_status", "escalations", ["status"])
    op.create_index("ix_escalations_user_id", "escalations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_escalations_user_id", table_name="escalations")
    op.drop_index("ix_escalations_status", table_name="escalations")
    op.drop_table("escalations")
    escalation_status.drop(op.get_bind(), checkfirst=True)
