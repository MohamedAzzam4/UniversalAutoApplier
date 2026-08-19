"""Add WQ-7C synthetic_mode flag to orchestration_runs.

Revision ID: 0014_orchestration_synthetic_mode
Revises: 0013_orchestration_durable_evidence
Create Date: 2026-08-19 00:00:00

Adds ``synthetic_orchestration`` (Boolean, NOT NULL, default False) so a
synthetic orchestration run is durably identifiable. Synthetic runs import a
pre-produced synthetic queue with WQ-7C markers, skip the production JobHunter
workflow, and target only newly imported application IDs. Existing rows
(created before this migration) are backfilled to False.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_orchestration_synthetic_mode"
down_revision: Union[str, None] = "0013_orchestration_durable_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("orchestration_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "synthetic_orchestration",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("orchestration_runs") as batch_op:
        batch_op.drop_column("synthetic_orchestration")