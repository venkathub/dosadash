"""menu_items.meal_periods: when a dish is typically served

Adds a JSONB list of meal periods ("breakfast" | "lunch" | "snacks" |
"dinner") so the order agent and the web menu can surface dishes for the
right time of day (live evals asked for breakfast suggestions and got
biryani). Backfills from the canonical seed by dish name — same pattern as
b7c4e92f1a05; admin-created dishes keep the empty-list default (shown in
every period).

Revision ID: d4a8c31f9e57
Revises: b7c4e92f1a05
Create Date: 2026-08-17
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4a8c31f9e57"
down_revision: str | None = "b7c4e92f1a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column(
            "meal_periods",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Backfill from the canonical menu seed (single source of truth for the
    # seeded catalog; admin-created dishes keep the empty default).
    from dosadash_ml.datagen import MENU_ITEMS

    for m in MENU_ITEMS:
        op.execute(
            sa.text(
                "UPDATE menu_items SET meal_periods = CAST(:periods AS jsonb) WHERE name = :name"
            ).bindparams(periods=json.dumps(list(m.meal_periods)), name=m.name)
        )
    # Hard serving windows for pongal dishes (breakfast counter, 06:00–12:00)
    # — only where an admin hasn't already configured a schedule.
    for m in MENU_ITEMS:
        if m.schedule is not None:
            op.execute(
                sa.text(
                    "UPDATE menu_items SET schedule = CAST(:schedule AS jsonb) "
                    "WHERE name = :name AND schedule IS NULL"
                ).bindparams(schedule=json.dumps(m.schedule), name=m.name)
            )


def downgrade() -> None:
    op.drop_column("menu_items", "meal_periods")
