"""menu_items.contains_onion_garlic: Jain-friendliness signal for the agent

The datagen always modeled this flag but it was dropped at seed time, so
the order agent had no Jain signal in its menu context (live eval ord-015
suggested Onion Dosa for a no-onion request). Backfills from the canonical
seed by dish name; new/unknown dishes default to true (contains onion/
garlic — the conservative assumption).

Revision ID: b7c4e92f1a05
Revises: a3f8d21c7b90
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4e92f1a05"
down_revision: str | None = "a3f8d21c7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "contains_onion_garlic",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Backfill from the canonical menu seed (single source of truth for the
    # seeded catalog; admin-created dishes keep the conservative default).
    from dosadash_ml.datagen import MENU_ITEMS

    jain_ok = [m.name for m in MENU_ITEMS if not m.contains_onion_garlic]
    if jain_ok:
        op.execute(
            sa.text("UPDATE menu_items SET contains_onion_garlic = false WHERE name IN :names")
            .bindparams(sa.bindparam("names", expanding=True))
            .bindparams(names=jain_ok)
        )


def downgrade() -> None:
    op.drop_column("menu_items", "contains_onion_garlic")
