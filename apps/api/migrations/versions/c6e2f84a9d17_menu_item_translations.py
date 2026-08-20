"""menu_item_translations: LLM-drafted, owner-verified menu localization (Phase 7)

Tamil-first localization store. One row per (menu item, language); drafts
come from the translation chain in apps/ai and only APPROVED rows are ever
served to customers. Prices/allergens/flags are never stored here — the
canonical English row stays the single source of truth.

Revision ID: c6e2f84a9d17
Revises: b5e8d24f7a91
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6e2f84a9d17"
down_revision: str | None = "b5e8d24f7a91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_item_translations",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_label", sa.String(length=80), nullable=True),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "APPROVED", "REJECTED", name="translation_status"),
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
            name=op.f("fk_menu_item_translations_item_id_menu_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_menu_item_translations_reviewed_by_users"),
        ),
        sa.PrimaryKeyConstraint("item_id", "lang", name=op.f("pk_menu_item_translations")),
    )


def downgrade() -> None:
    op.drop_table("menu_item_translations")
    sa.Enum(name="translation_status").drop(op.get_bind(), checkfirst=True)
