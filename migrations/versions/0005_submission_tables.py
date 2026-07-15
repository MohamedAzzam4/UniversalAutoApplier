"""Add submission_approvals, submission_claims, submission_results tables.

Revision ID: 0005_submission_tables
Revises: 0004_intervention_llm_metadata
Create Date: 2026-07-15 05:00:00

Creates three new tables for the controlled-final-submission workpackage:

- submission_approvals: one-time approvals tied to a snapshot hash.
- submission_claims: transactional one-time locks preventing duplicate clicks.
- submission_results: persisted audit record of every submission attempt.

Also adds the ``submission_approvals`` relationship to ``application_jobs``.

These tables are new — no existing data is modified.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_submission_tables"
down_revision: Union[str, None] = "0004_intervention_llm_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "submission_approvals",
        sa.Column("approval_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(length=64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False, index=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "submission_claims",
        sa.Column("claim_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(length=64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "approval_id",
            sa.String(length=64),
            sa.ForeignKey("submission_approvals.approval_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_state", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "submission_results",
        sa.Column("result_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(length=64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("approval_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("snapshot_hash_at_submit", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("clicked", sa.Boolean(), nullable=False, default=False),
        sa.Column("pre_submit_screenshot", sa.Text(), nullable=True),
        sa.Column("post_submit_screenshot", sa.Text(), nullable=True),
        sa.Column("post_submit_url", sa.Text(), nullable=True),
        sa.Column("post_submit_dom_path", sa.Text(), nullable=True),
        sa.Column("confirmation_evidence", sa.Text(), nullable=True),
        sa.Column("validation_errors_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("submission_results")
    op.drop_table("submission_claims")
    op.drop_table("submission_approvals")
