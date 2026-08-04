"""Add structured ATS reference column to submission_results (WQ-1).

Revision ID: 0007_submission_results_ats_reference
Revises: 0006_submission_unique_constraints
Create Date: 2026-08-04 12:00:00

WQ-1: ``SubmissionResult`` and ``SubmissionResultRow`` gain an optional
structured ``ats_reference_id`` field. This is the ONLY durable path to
``APPLIED`` — it must be a reliable structured ATS application/reference ID,
never text parsed from page content or logs.

The column is a plain TEXT column with a constant server default of empty
string, so pre-existing rows get ``''`` (never NULL) and raw SQL inserts stay
safe. No batch mode is needed: SQLite supports ``ALTER TABLE ADD COLUMN``
directly for a column with a constant default.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_submission_results_ats_reference"
down_revision: Union[str, None] = "0006_submission_unique_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submission_results",
        sa.Column("ats_reference_id", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("submission_results", "ats_reference_id")
