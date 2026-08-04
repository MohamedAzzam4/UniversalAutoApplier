"""Reconcile pre-WQ-1 submission rows into consistent job statuses (WQ-1).

Revision ID: 0008_reconcile_submission_statuses
Revises: 0007_submission_results_ats_reference
Create Date: 2026-08-04 12:00:00

Before WQ-1, ``submission_results`` rows were persisted without any job
status transition, so databases could hold a job in ``review_ready`` while a
``submitted_confirmed`` or ``outcome_unknown`` result existed. This one-time
data repair brings those databases in line with the explicit policy in
``submission/status_transitions.py``:

- ``submitted_confirmed`` without a structured ATS reference, on a
  ``review_ready`` job  ->  job ``submitted``.
- ``outcome_unknown`` on a ``review_ready`` or ``submitted`` job
  ->  job ``needs_review`` (explicit human review).

Safety bounds (matching the runtime policy):

- Jobs whose status is NOT ``review_ready`` or ``submitted`` are never
  touched: terminal statuses (``applied``, ``rejected``, ``skipped``,
  ``closed``) are never downgraded, and earlier pipeline statuses are never
  auto-advanced.
- ``APPLIED`` is NEVER inferred for legacy rows: pre-WQ-1 results have no
  durable structured reference, so legacy ``submitted_confirmed`` rows can
  only ever establish ``SUBMITTED``.

Execution model: Alembic runs this exactly once per database, inside the
same transaction as the schema changes, when the database is upgraded to
this revision (startup, ``alembic upgrade head``, or ``apply_migrations``).
On a fresh database there are no legacy rows and the UPDATEs are no-ops. The
WHERE guards make the statements idempotent even if replayed; after this
migration, the application code never creates inconsistent rows again.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_reconcile_submission_statuses"
down_revision: Union[str, None] = "0007_submission_results_ats_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # submitted_confirmed without a structured ATS reference:
    # review_ready -> submitted. Rows that already have an ats_reference_id
    # are NOT upgraded to applied: the reference predates the durable field
    # and cannot be trusted as structured data.
    bind.execute(
        sa.text(
            """
            UPDATE application_jobs
            SET status = 'submitted'
            WHERE status = 'review_ready'
              AND application_id IN (
                SELECT application_id
                FROM submission_results
                WHERE state = 'submitted_confirmed'
                  AND (ats_reference_id IS NULL OR ats_reference_id = '')
              )
            """
        )
    )

    # outcome_unknown on review_ready or submitted: needs_review.
    bind.execute(
        sa.text(
            """
            UPDATE application_jobs
            SET status = 'needs_review'
            WHERE status IN ('review_ready', 'submitted')
              AND application_id IN (
                SELECT application_id
                FROM submission_results
                WHERE state = 'outcome_unknown'
              )
            """
        )
    )


def downgrade() -> None:
    # Data repair cannot be reversed: the reconciled statuses are the correct
    # interpretation of the persisted results. Nothing to undo.
    pass
