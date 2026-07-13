"""Add documents_json column to application_jobs.

Revision ID: 0003_application_job_documents
Revises: 0002_application_job_optional_fields
Create Date: 2026-07-13 01:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_application_job_documents"
down_revision: Union[str, None] = "0002_application_job_optional_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "application_jobs",
        sa.Column("documents_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_jobs", "documents_json")
