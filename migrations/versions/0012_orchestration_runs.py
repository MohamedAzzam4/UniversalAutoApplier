"""Add orchestration_runs table (WQ-6 cross-repository orchestration).

Revision ID: 0012_orchestration_runs
Revises: 0011_pipeline_worker_liveness
Create Date: 2026-08-10 00:00:00

New table ``orchestration_runs`` — one durable row per cross-repository
orchestration run (JobHunter export -> UAA import -> UAA pipeline). The row
persists the mode (sequential/parallel), the overall phase, the JobHunter
child PID and exit code, the queue-import run id, the UAA pipeline run id,
bounded errors, timestamps, and the cancellation reason.

Only one active orchestration run may exist at a time (enforced by the
service layer's in-process lock plus the ``status`` column: terminal runs
are never reused).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_orchestration_runs"
down_revision: Union[str, None] = "0011_pipeline_worker_liveness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orchestration_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        # sequential | parallel
        sa.Column("mode", sa.String(16), nullable=False, index=True),
        # idle | running | jobhunter_running | importing | pipeline_running
        # | completed | failed | cancelled
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("current_phase", sa.String(64), nullable=False, default=""),
        sa.Column("last_action", sa.Text, nullable=False, default=""),
        sa.Column("last_error", sa.Text, nullable=False, default=""),
        sa.Column("cancel_reason", sa.Text, nullable=False, default=""),
        # JobHunter child process liveness
        sa.Column("jobhunter_pid", sa.Integer(), nullable=True),
        sa.Column("jobhunter_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobhunter_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobhunter_exit_code", sa.Integer(), nullable=True),
        # Bounded stdout/stderr capture (secrets filtered by the service)
        sa.Column("jobhunter_stdout", sa.Text, nullable=False, default=""),
        sa.Column("jobhunter_stderr", sa.Text, nullable=False, default=""),
        # Queue import result
        sa.Column("queue_import_run_id", sa.String(64), nullable=True),
        sa.Column("queue_import_state", sa.String(16), nullable=True),
        sa.Column("queue_imported", sa.Integer(), nullable=True, default=0),
        sa.Column("queue_skipped", sa.Integer(), nullable=True, default=0),
        # UAA pipeline run link
        sa.Column("pipeline_run_id", sa.String(64), nullable=True),
        sa.Column("pipeline_state", sa.String(32), nullable=True),
        # Bounded structured errors
        sa.Column("errors_json", sa.JSON(), nullable=False, default=list),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("orchestration_runs")
