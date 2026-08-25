"""fixer_runs: workflow self-reported run telemetry (Phase 14 slice 3)

The fixer/verifier workflows POST their own run outcome to the api
(eval_runs CI-ingest pattern) — run-level truth GitHub webhooks cannot
carry (a run that died without a PR is otherwise invisible).

Revision ID: d9e6f24a8b35
Revises: c7d5e83f9a26
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e6f24a8b35"
down_revision: str | None = "c7d5e83f9a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fixer_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow", sa.String(length=10), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("conclusion", sa.String(length=30), nullable=False),
        sa.Column("trigger_label", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["feedback_reports.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow", "run_id", "run_attempt"),
    )
    op.create_index("ix_fixer_runs_report_id", "fixer_runs", ["report_id"], unique=False)
    op.create_index("ix_fixer_runs_run_id", "fixer_runs", ["run_id"], unique=False)
    op.create_index("ix_fixer_runs_issue_number", "fixer_runs", ["issue_number"], unique=False)
    op.create_index("ix_fixer_runs_created_at", "fixer_runs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fixer_runs_created_at", table_name="fixer_runs")
    op.drop_index("ix_fixer_runs_issue_number", table_name="fixer_runs")
    op.drop_index("ix_fixer_runs_run_id", table_name="fixer_runs")
    op.drop_index("ix_fixer_runs_report_id", table_name="fixer_runs")
    op.drop_table("fixer_runs")
