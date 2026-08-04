"""Reconcile pre-WQ-1 submission rows into consistent job statuses (WQ-1).

Revision ID: 0008_reconcile_submission_statuses
Revises: 0007_submission_results_ats_reference
Create Date: 2026-08-04 12:00:00

Before WQ-1, ``submission_results`` rows were persisted without any job
status transition, so databases could hold a job in ``review_ready`` while
one or more ``submitted_confirmed`` / ``outcome_unknown`` results existed.

This one-time data repair brings those databases in line with the explicit
policy in ``submission/status_transitions.py``, using ONLY the LATEST
persisted result per application:

- latest ``submitted_confirmed`` without a structured ATS reference, on a
  ``review_ready`` job  ->  job ``submitted``.
- latest ``outcome_unknown`` on a ``review_ready`` or ``submitted`` job
  ->  job ``needs_review`` (explicit human review).
- every other latest result state  ->  no change.

The latest result is selected deterministically:

1. ``attempted_at`` DESCENDING (the persisted attempt timestamp)
2. ``result_id`` DESCENDING as the tie-breaker (stable, never depends on
   insertion or SQLite rowid order)

So an old ``outcome_unknown`` can never override a newer
``submitted_confirmed`` result (and vice versa): the repair is driven by the
most recent outcome only.

Safety bounds (matching the runtime policy):

- Jobs whose status is NOT ``review_ready`` or ``submitted`` are never
  touched: terminal statuses (``applied``, ``rejected``, ``skipped``,
  ``closed``) are never downgraded, and earlier pipeline statuses are never
  auto-advanced.
- ``APPLIED`` is NEVER inferred for legacy rows: ``ats_reference_id`` only
  became durable after migration 0007, so a reference carried by a legacy row
  is not verified structured data and the repair will not act on it. A
  legacy ``submitted_confirmed`` result WITHOUT a reference establishes
  ``SUBMITTED``; one WITH an unverified reference is left untouched (it is
  already ``SUBMITTED``/``APPLIED`` at application level in the runtime
  path, and never downgraded here).

Implementation: uses ``ROW_NUMBER() OVER (PARTITION BY application_id ORDER
BY attempted_at DESC, result_id DESC)`` inside a common-table-expression
prefixed UPDATE. SQLite has supported window functions since 3.25 (bundled
with every Python version this repo supports), so the SQL is
SQLite-compatible. The statements are exported as
``LEGACY_RECONCILIATION_STATEMENTS`` so tests can re-run the exact
production logic to prove idempotency.

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

# One row per application, ranked by attempted_at DESC then result_id DESC.
LATEST_RESULT_RANKING = """
    WITH ranked AS (
        SELECT
            application_id,
            state,
            ats_reference_id,
            ROW_NUMBER() OVER (
                PARTITION BY application_id
                ORDER BY attempted_at DESC, result_id DESC
            ) AS rn
        FROM submission_results
    )
"""

# Bounded, idempotent repair statements. Exported so the contract tests can
# re-run the exact production SQL to prove idempotency.
LEGACY_RECONCILIATION_STATEMENTS: tuple[str, ...] = (
    LATEST_RESULT_RANKING
    + """
    UPDATE application_jobs
    SET status = 'submitted'
    WHERE status = 'review_ready'
      AND application_id IN (
        SELECT application_id
        FROM ranked
        WHERE rn = 1
          AND state = 'submitted_confirmed'
          AND (ats_reference_id IS NULL OR ats_reference_id = '')
      )
    """,
    LATEST_RESULT_RANKING
    + """
    UPDATE application_jobs
    SET status = 'needs_review'
    WHERE status IN ('review_ready', 'submitted')
      AND application_id IN (
        SELECT application_id
        FROM ranked
        WHERE rn = 1 AND state = 'outcome_unknown'
      )
    """,
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in LEGACY_RECONCILIATION_STATEMENTS:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    # Data repair cannot be reversed: the reconciled statuses are the correct
    # interpretation of the persisted results. Nothing to undo.
    pass