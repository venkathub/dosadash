"""suppliers + purchase_orders + wastage_log (Phase 6 inventory)

- `suppliers`: master table promoted from the free-text `ingredients.supplier`
  column — backfilled here from distinct existing values; the text column is
  kept for display back-compat.
- `ingredients.supplier_id`: canonical FK link (SET NULL on supplier delete).
- `purchase_orders` + `purchase_order_items`: inventory-agent drafts with the
  owner-approval lifecycle (DRAFT → PENDING_APPROVAL → APPROVED → RECEIVED,
  plus REJECTED/CANCELLED) and agent provenance (model, prompt_version).
- `wastage_log`: stock write-offs; each row snapshots `stock_after`.

Revision ID: c9d4e82f7a13
Revises: b3f9c82d4e61
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d4e82f7a13"
down_revision: str | None = "b3f9c82d4e61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

po_state = sa.Enum(
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "RECEIVED",
    "CANCELLED",
    name="po_state",
)
po_source = sa.Enum("AGENT", "MANUAL", name="po_source")
wastage_reason = sa.Enum(
    "SPOILAGE", "PREP_LOSS", "SPILLAGE", "EXPIRED", "OTHER", name="wastage_reason"
)


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("email", sa.String(length=120), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    op.add_column("ingredients", sa.Column("supplier_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_ingredients_supplier_id",
        "ingredients",
        "suppliers",
        ["supplier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ingredients_supplier_id", "ingredients", ["supplier_id"])

    # Backfill: one supplier row per distinct legacy free-text name, then link.
    op.execute(
        """
        INSERT INTO suppliers (name)
        SELECT DISTINCT trim(supplier) FROM ingredients
        WHERE supplier IS NOT NULL AND trim(supplier) <> ''
        ORDER BY 1
        """
    )
    op.execute(
        """
        UPDATE ingredients i SET supplier_id = s.id
        FROM suppliers s
        WHERE i.supplier IS NOT NULL AND trim(i.supplier) = s.name
        """
    )

    # Enum types are auto-created by the create_table calls below.
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "supplier_id",
            sa.BigInteger(),
            sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", po_state, nullable=False, server_default="DRAFT"),
        sa.Column("source", po_source, nullable=False, server_default="AGENT"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("coverage_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("expected_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])

    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "po_id",
            sa.BigInteger(),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ingredient_id", sa.BigInteger(), sa.ForeignKey("ingredients.id"), nullable=False
        ),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("unit_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.UniqueConstraint("po_id", "ingredient_id", name="uq_po_items_po_id_ingredient_id"),
    )
    op.create_index("ix_purchase_order_items_po_id", "purchase_order_items", ["po_id"])
    op.create_index(
        "ix_purchase_order_items_ingredient_id", "purchase_order_items", ["ingredient_id"]
    )

    op.create_table(
        "wastage_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "ingredient_id", sa.BigInteger(), sa.ForeignKey("ingredients.id"), nullable=False
        ),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("reason", wastage_reason, nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("recorded_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("stock_after", sa.Numeric(12, 3), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_wastage_log_ingredient_id", "wastage_log", ["ingredient_id"])
    op.create_index("ix_wastage_log_at", "wastage_log", ["at"])


def downgrade() -> None:
    op.drop_index("ix_wastage_log_at", table_name="wastage_log")
    op.drop_index("ix_wastage_log_ingredient_id", table_name="wastage_log")
    op.drop_table("wastage_log")
    op.drop_index("ix_purchase_order_items_ingredient_id", table_name="purchase_order_items")
    op.drop_index("ix_purchase_order_items_po_id", table_name="purchase_order_items")
    op.drop_table("purchase_order_items")
    op.drop_index("ix_purchase_orders_supplier_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_status", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    wastage_reason.drop(op.get_bind(), checkfirst=True)
    po_source.drop(op.get_bind(), checkfirst=True)
    po_state.drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_ingredients_supplier_id", table_name="ingredients")
    op.drop_constraint("fk_ingredients_supplier_id", "ingredients", type_="foreignkey")
    op.drop_column("ingredients", "supplier_id")
    op.drop_table("suppliers")
