"""invoices — supplier invoice OCR review queue (Phase 6)

VLM extraction + PO match stored as JSONB provenance; `confidence` drives
the review-queue gate (MATCHED vs PENDING_REVIEW). APPROVED marks the
linked purchase order RECEIVED (stock update happens through po_service,
never here).

Revision ID: d7f3a91c5e24
Revises: c9d4e82f7a13
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d7f3a91c5e24"
down_revision: str | None = "c9d4e82f7a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

invoice_status = sa.Enum("PENDING_REVIEW", "MATCHED", "APPROVED", "REJECTED", name="invoice_status")


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("status", invoice_status, nullable=False, server_default="PENDING_REVIEW"),
        sa.Column(
            "po_id",
            sa.BigInteger(),
            sa.ForeignKey("purchase_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("extraction", JSONB(), nullable=True),
        sa.Column("match", JSONB(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("uploaded_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_po_id", "invoices", ["po_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_po_id", table_name="invoices")
    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_table("invoices")
    invoice_status.drop(op.get_bind(), checkfirst=True)
