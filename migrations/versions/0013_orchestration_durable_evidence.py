"""Add durable batch-evidence columns to orchestration_runs (WQ-6 round 7).

Revision ID: 0013_orchestration_durable_evidence
Revises: 0012_orchestration_runs
Create Date: 2026-08-11 00:00:00

These columns persist the durable per-batch evidence required by WQ-6 round 7:

- ``targeted_ids_json`` — the original set of target application IDs that the
  multi-batch continuation loop was launched with. Set once when the loop
  starts. This is the contract: every ID in this list must be processed
  exactly once before the run is "completed".
- ``processed_ids_json`` — the subset of targeted IDs that have been
  processed by a pipeline pass (left READY_TO_APPLY/QUEUED). Updated after
  every batch.
- ``remaining_ids_json`` — the subset of targeted IDs still eligible for
  processing (still READY_TO_APPLY/QUEUED). Updated after every batch.
- ``targeted_count`` / ``processed_count`` / ``remaining_count`` —
  pre-computed counts for fast API reads (the JSON columns are the source
  of truth; the counts are denormalized for convenience).
- ``pipeline_run_ids_json`` — the ordered list of every continuation
  pipeline run ID, in execution order. Updated after every batch.
- ``pass_count`` — the number of pipeline passes that have been completed.
  Updated after every batch.

These columns are nullable so existing rows (created before this migration)
remain valid. The service treats ``None`` as "not yet set" (zero/empty)
consistently.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_orchestration_durable_evidence"
down_revision: Union[str, None] = "0012_orchestration_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("orchestration_runs") as batch_op:
        batch_op.add_column(
            sa.Column("targeted_ids_json", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("processed_ids_json", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("remaining_ids_json", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("targeted_count", sa.Integer(), nullable=True, default=0)
        )
        batch_op.add_column(
            sa.Column("processed_count", sa.Integer(), nullable=True, default=0)
        )
        batch_op.add_column(
            sa.Column("remaining_count", sa.Integer(), nullable=True, default=0)
        )
        batch_op.add_column(
            sa.Column("pipeline_run_ids_json", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pass_count", sa.Integer(), nullable=True, default=0)
        )


def downgrade() -> None:
    with op.batch_alter_table("orchestration_runs") as batch_op:
        batch_op.drop_column("targeted_ids_json")
        batch_op.drop_column("processed_ids_json")
        batch_op.drop_column("remaining_ids_json")
        batch_op.drop_column("targeted_count")
        batch_op.drop_column("processed_count")
        batch_op.drop_column("remaining_count")
        batch_op.drop_column("pipeline_run_ids_json")
        batch_op.drop_column("pass_count")
