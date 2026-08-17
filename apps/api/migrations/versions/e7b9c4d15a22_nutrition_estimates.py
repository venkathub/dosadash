"""nutrition_estimates: LLM-drafted, owner-verified nutrition facts

Revision ID: e7b9c4d15a22
Revises: c41f7a2d9b03
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b9c4d15a22"
down_revision: str | None = "c41f7a2d9b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nutrition_estimates",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "APPROVED", "REJECTED", name="nutrition_status"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_nutrition_estimates_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_nutrition_estimates_reviewed_by_users"),
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_nutrition_estimates")),
    )


def downgrade() -> None:
    op.drop_table("nutrition_estimates")
    sa.Enum(name="nutrition_status").drop(op.get_bind(), checkfirst=True)
