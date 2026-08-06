"""Add pipeline_runs table (WQ-4 durable background-pipeline history).

Revision ID: 0010_pipeline_runs
Revises: 0009_queue_import_runs
Create Date: 2026-08-05 09:00:00

New table owned by the pipeline worker service. No existing fields are
modified. Contains the authoritative restart-safe snapshot of each run:
run id, status, current job/phase, last action and error, progress counts,
structured job errors, cancellation reason, and start/finish timestamps.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_pipeline_runs"
down_revision: Union[str, None] = "0009_queue_import_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        # idle | running | pausing | paused | cancelling | cancelled | completed | failed
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("mode", sa.String(64), nullable=False, server_default="sequential_dry_run"),
        sa.Column("current_job_id", sa.String(64), nullable=True),
        sa.Column("current_phase", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("cancel_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("jobs_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("pipeline_runs")