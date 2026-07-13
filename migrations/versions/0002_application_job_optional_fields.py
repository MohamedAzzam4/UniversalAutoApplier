"""Add job_id, external_job_id, and optional descriptive fields to application_jobs.

Revision ID: 0002_application_job_optional_fields
Revises: 0001_initial_schema
Create Date: 2026-07-13 00:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_application_job_optional_fields"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add optional identity fields (needed to recompute application_id on read).
    op.add_column("application_jobs", sa.Column("job_id", sa.String(length=256), nullable=True))
    op.add_column(
        "application_jobs",
        sa.Column("external_job_id", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "ix_application_jobs_external_job_id",
        "application_jobs",
        ["external_job_id"],
    )

    # Add optional descriptive fields from the ApplicationJob contract.
    op.add_column(
        "application_jobs", sa.Column("date_posted", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "application_jobs",
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_jobs",
        sa.Column("tailored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "application_jobs", sa.Column("evaluation_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "application_jobs",
        sa.Column("german_filter_result", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_jobs", "german_filter_result")
    op.drop_column("application_jobs", "evaluation_reason")
    op.drop_column("application_jobs", "tailored_at")
    op.drop_column("application_jobs", "evaluated_at")
    op.drop_column("application_jobs", "date_posted")
    op.drop_index("ix_application_jobs_external_job_id", table_name="application_jobs")
    op.drop_column("application_jobs", "external_job_id")
    op.drop_column("application_jobs", "job_id")
