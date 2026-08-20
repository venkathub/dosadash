"""menu_image_drafts + menu_items.image_ai: AI dish photos, owner-verified (Phase 7)

Generated images land as DRAFT files under the api media dir; approval sets
menu_items.image_url and marks image_ai = true so the customer UI always
labels synthetic photos (AI-labeled is a docs/05 requirement, not a nicety).

Revision ID: d8f1a63c9e25
Revises: c6e2f84a9d17
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f1a63c9e25"
down_revision: str | None = "c6e2f84a9d17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_image_drafts",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "APPROVED", "REJECTED", name="image_draft_status"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["menu_items.id"],
            name=op.f("fk_menu_image_drafts_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_menu_image_drafts_reviewed_by_users"),
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_menu_image_drafts")),
    )
    op.add_column(
        "menu_items",
        sa.Column("image_ai", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("menu_items", "image_ai")
    op.drop_table("menu_image_drafts")
    sa.Enum(name="image_draft_status").drop(op.get_bind(), checkfirst=True)
