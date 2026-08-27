"""fixer_runs usage telemetry (Phase 15 S7, docs/15)

Cache/cost columns for the fixer/verifier run ingest: the workflows parse
the action's execution file best-effort and report the run's real token
usage — this is how the within-run prompt-cache share and loop spend
become dashboard numbers instead of vibes. All nullable: outcome truth
outranks telemetry.

Revision ID: a8e3d76c5f92
Revises: f7d2c85b9a34
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8e3d76c5f92"
down_revision: str | None = "f7d2c85b9a34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("cost_usd", sa.Float()),
    ("input_tokens", sa.BigInteger()),
    ("cache_read_tokens", sa.BigInteger()),
    ("cache_creation_tokens", sa.BigInteger()),
    ("output_tokens", sa.BigInteger()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("fixer_runs", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("fixer_runs", name)
