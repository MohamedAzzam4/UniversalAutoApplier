"""Add llm_metadata_json column to interventions.

Revision ID: 0004_intervention_llm_metadata
Revises: 0003_application_job_documents
Create Date: 2026-07-14 18:00:00

Stores structured LLM question-resolution metadata that cannot fit in
the existing typed columns. The JSON object may contain:
- available_options: list[str]
- evidence_summary: str
- category: str (QuestionCategory)
- risk_level: str (QuestionRisk)
- requires_confirmation: bool
- unresolved_reason: str
- field_token: str
- answer_source: str (deterministic_mapper, llm_grounded, explicit_metadata, answer_memory)

Existing intervention rows remain valid — the new column is nullable.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_intervention_llm_metadata"
down_revision: Union[str, None] = "0003_application_job_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "interventions",
        sa.Column("llm_metadata_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interventions", "llm_metadata_json")
