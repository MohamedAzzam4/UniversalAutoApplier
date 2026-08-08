"""Add worker liveness columns to pipeline_runs (WQ-5 stale-run recovery).

Revision ID: 0011_pipeline_worker_liveness
Revises: 0010_pipeline_runs
Create Date: 2026-08-06 10:00:00

New nullable columns on the existing ``pipeline_runs`` table:
- ``worker_pid`` — OS pid of the worker subprocess that owns the run.
- ``worker_started_at`` — when the worker subprocess was launched.
- ``heartbeat_at`` — last time the worker proved it was alive (updated
  continuously, including while paused/waiting).

These make a run's ownership provable across restarts: a run is stale only
when its worker pid is missing/dead AND its heartbeat has expired. Existing
rows are unaffected (columns are nullable).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_pipeline_worker_liveness"
down_revision: Union[str, None] = "0010_pipeline_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("worker_pid", sa.Integer(), nullable=True))
    op.add_column(
        "pipeline_runs",
        sa.Column("worker_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "heartbeat_at")
    op.drop_column("pipeline_runs", "worker_started_at")
    op.drop_column("pipeline_runs", "worker_pid")
