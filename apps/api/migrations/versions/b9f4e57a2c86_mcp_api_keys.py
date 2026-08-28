"""mcp_api_keys (Phase 16, docs/09)

Admin-issued API keys for the hosted remote MCP endpoint (/mcp on the ai
service): SHA-256 hash only (plaintext shown once in the admin GUI),
display prefix for list views, revoked_at instead of deletion so the
audit story outlives the key.

Revision ID: b9f4e57a2c86
Revises: a8e3d76c5f92
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9f4e57a2c86"
down_revision: str | None = "a8e3d76c5f92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_api_keys",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index(op.f("ix_mcp_api_keys_created_by"), "mcp_api_keys", ["created_by"])


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_api_keys_created_by"), table_name="mcp_api_keys")
    op.drop_table("mcp_api_keys")
