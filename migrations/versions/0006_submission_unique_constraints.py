"""Add unique constraints on submission_claims.approval_id and submission_results.approval_id.

Revision ID: 0006_submission_unique_constraints
Revises: 0005_submission_tables
Create Date: 2026-07-15 06:00:00

Adds database-enforced uniqueness constraints so that:
- Only ONE claim can ever exist per approval (prevents duplicate submit
  clicks under concurrent requests).
- Only ONE result can ever exist per approval (prevents duplicate audit
  records).

These constraints are enforced at the database level, not just in
application code. This means even if two concurrent requests race past
the SELECT-then-INSERT check, the database will reject the second
INSERT with an IntegrityError.

The application code in submission/store.py catches IntegrityError and
returns the existing row (idempotent response for duplicate requests).

Uses Alembic batch mode for SQLite compatibility (SQLite does not
support ALTER TABLE ADD CONSTRAINT directly; batch mode copies the
table, creates the new one with constraints, and moves data).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_submission_unique_constraints"
down_revision: Union[str, None] = "0005_submission_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch mode for SQLite compatibility. SQLite does not support
    # ALTER TABLE ADD CONSTRAINT; batch mode recreates the table with
    # the new constraint.
    with op.batch_alter_table("submission_claims", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_submission_claims_approval_id",
            ["approval_id"],
        )

    with op.batch_alter_table("submission_results", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_submission_results_approval_id",
            ["approval_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("submission_results", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_submission_results_approval_id",
            type_="unique",
        )

    with op.batch_alter_table("submission_claims", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_submission_claims_approval_id",
            type_="unique",
        )
