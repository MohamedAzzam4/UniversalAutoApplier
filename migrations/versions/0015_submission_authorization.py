"""Add WQ-8 single-use real-submission authorization table.

Revision ID: 0015_submission_authorization
Revises: 0014_orchestration_synthetic_mode
Create Date: 2026-08-20 00:00:00

Adds ``submission_authorizations`` — the tighter, single-use real-submission
authorization (WQ-8). Bound to the application/job identity/URL, the frozen
``review_plan_hash`` and the CV/document content hashes, with an expiry and
one-time consumed/revoked state. Default remains submission forbidden; the
table is empty until an owner issues a Phase B authorization.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_submission_authorization"
down_revision: Union[str, None] = "0014_orchestration_synthetic_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "submission_authorizations",
        sa.Column("authorization_id", sa.String(64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
        ),
        sa.Column("application_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("job_company", sa.String(256), nullable=False, server_default=""),
        sa.Column("job_title", sa.String(256), nullable=False, server_default=""),
        sa.Column("review_plan_hash", sa.String(64), nullable=False),
        sa.Column("document_hashes_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_submission_authorizations_application_id",
        "submission_authorizations",
        ["application_id"],
    )
    op.create_index(
        "ix_submission_authorizations_review_plan_hash",
        "submission_authorizations",
        ["review_plan_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_submission_authorizations_review_plan_hash", table_name="submission_authorizations")
    op.drop_index("ix_submission_authorizations_application_id", table_name="submission_authorizations")
    op.drop_table("submission_authorizations")