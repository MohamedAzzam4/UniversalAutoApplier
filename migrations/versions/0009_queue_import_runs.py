"""Add queue_import_runs table (WQ-3 durable import-run history).

Revision ID: 0009_queue_import_runs
Revises: 0008_reconcile_submission_statuses
Create Date: 2026-08-05 08:00:00

Records every import of the configured JobHunter queue so history survives
restart: run id, source path, source file fingerprint (sha256 when readable),
started/completed timestamps, result state (success/partial/failed/skipped),
counts, structured row errors, and a safe human-readable failure reason.

This is a NEW table — no existing job, submission, or intervention data is
modified. Only raw line numbers and error messages are persisted; the raw
JSONL line (which may carry candidate data) is never stored.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_queue_import_runs"
down_revision: Union[str, None] = "0008_reconcile_submission_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "queue_import_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, index=True),
        sa.Column("total_lines", sa.Integer(), nullable=False, default=0),
        sa.Column("imported", sa.Integer(), nullable=False, default=0),
        sa.Column("skipped", sa.Integer(), nullable=False, default=0),
        sa.Column("error_count", sa.Integer(), nullable=False, default=0),
        sa.Column("row_errors_json", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("queue_import_runs")