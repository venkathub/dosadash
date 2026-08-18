"""read-only copilot role (Phase 5 analytics copilot — docs/03 #21)

Creates `dosadash_readonly` with SELECT on exactly the copilot's table
allowlist — DB-level defense in depth behind the SQL validation guardrail
and the READ ONLY transaction.

Opt-in: the role is only created/updated when READONLY_DB_PASSWORD is set
in the migration environment (compose passes it to the api container).
Without it this migration is a no-op — the copilot then runs on the main
role, still guarded by validation + read-only transactions.

Revision ID: b3f9c82d4e61
Revises: a9e4b71c3d58
Create Date: 2026-08-18
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "b3f9c82d4e61"
down_revision: str | None = "a9e4b71c3d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match dosadash_ai.copilot.guardrail.ALLOWED_TABLES
COPILOT_TABLES = [
    "orders",
    "order_items",
    "menu_items",
    "ingredients",
    "recipe_ingredients",
    "forecasts",
    "customer_segments",
    "coupons",
    "coupon_redemptions",
    "combos",
    "users",
    "eval_runs",
]

ROLE = "dosadash_readonly"


def upgrade() -> None:
    password = os.environ.get("READONLY_DB_PASSWORD", "")
    if not password:
        print(f"readonly role: READONLY_DB_PASSWORD not set — skipping {ROLE} creation")
        return
    escaped = password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} LOGIN PASSWORD '{escaped}';
            ELSE
                ALTER ROLE {ROLE} WITH LOGIN PASSWORD '{escaped}';
            END IF;
        END $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE}")
    for table in COPILOT_TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {ROLE}")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}';
                EXECUTE 'REVOKE USAGE ON SCHEMA public FROM {ROLE}';
                EXECUTE 'DROP ROLE {ROLE}';
            END IF;
        END $$;
        """
    )
