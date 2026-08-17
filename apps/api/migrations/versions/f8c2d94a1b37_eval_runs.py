"""eval_runs: live eval scoreboard (Phase 4 LLMOps)

One row per live eval run (evals/suites/run_live_evals.py). CI posts the
results JSON after every gate run — pass or fail — so the admin
scoreboard shows accuracy/safety trends over time. Headline metrics are
promoted to columns; per-case drill-down lives in JSONB.

Revision ID: f8c2d94a1b37
Revises: d4a8c31f9e57
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f8c2d94a1b37"
down_revision: str | None = "d4a8c31f9e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("order_accuracy", sa.Float(), nullable=False),
        sa.Column("tool_correctness", sa.Float(), nullable=False),
        sa.Column("guardrail_bypasses", sa.Integer(), nullable=False),
        sa.Column("guardrail_cases", sa.Integer(), nullable=False),
        sa.Column("tone", sa.Float(), nullable=True),
        sa.Column("gates_passed", sa.Boolean(), nullable=False),
        sa.Column("failures", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("case_reports", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_eval_runs_ran_at", "eval_runs", ["ran_at"])
    op.create_index("ix_eval_runs_gates_passed", "eval_runs", ["gates_passed"])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_gates_passed", table_name="eval_runs")
    op.drop_index("ix_eval_runs_ran_at", table_name="eval_runs")
    op.drop_table("eval_runs")
