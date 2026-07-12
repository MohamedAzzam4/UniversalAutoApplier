"""Initial schema: application_jobs, application_attempts, phase_results,
interventions, answer_memories, artifacts, system_runs.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-12 00:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # application_jobs
    op.create_table(
        "application_jobs",
        sa.Column("application_id", sa.String(length=64), primary_key=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("company", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=256), nullable=True),
        sa.Column("job_description", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("cv_pdf", sa.Text(), nullable=True),
        sa.Column("cover_letter_pdf", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("ix_application_jobs_platform", "application_jobs", ["platform"])
    op.create_index("ix_application_jobs_source", "application_jobs", ["source"])
    op.create_index("ix_application_jobs_status", "application_jobs", ["status"])

    # application_attempts
    op.create_table(
        "application_attempts",
        sa.Column("attempt_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(length=64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_phase", sa.String(length=32), nullable=True),
        sa.Column("submit_approval_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_application_attempts_application_id",
        "application_attempts",
        ["application_id"],
    )
    op.create_index("ix_application_attempts_run_id", "application_attempts", ["run_id"])
    op.create_index("ix_application_attempts_status", "application_attempts", ["status"])

    # phase_results
    op.create_table(
        "phase_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "attempt_id",
            sa.String(length=64),
            sa.ForeignKey("application_attempts.attempt_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("screenshot", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_phase_results_attempt_id",
        "phase_results",
        ["attempt_id"],
    )

    # interventions
    op.create_table(
        "interventions",
        sa.Column("intervention_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(length=64),
            sa.ForeignKey("application_jobs.application_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("suggested_answer", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("field_selector", sa.Text(), nullable=True),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("screenshot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_interventions_application_id",
        "interventions",
        ["application_id"],
    )
    op.create_index("ix_interventions_status", "interventions", ["status"])

    # answer_memories
    op.create_table(
        "answer_memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "normalized_question",
            sa.String(length=256),
            nullable=False,
            unique=True,
        ),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_answer_memories_normalized_question",
        "answer_memories",
        ["normalized_question"],
    )

    # artifacts
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "attempt_id",
            sa.String(length=64),
            sa.ForeignKey("application_attempts.attempt_id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_attempt_id", "artifacts", ["attempt_id"])

    # system_runs
    op.create_table(
        "system_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submit_mode", sa.String(length=32), nullable=False),
        sa.Column("headless", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("system_runs")
    op.drop_index("ix_artifacts_attempt_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_answer_memories_normalized_question", table_name="answer_memories")
    op.drop_table("answer_memories")
    op.drop_index("ix_interventions_status", table_name="interventions")
    op.drop_index("ix_interventions_application_id", table_name="interventions")
    op.drop_table("interventions")
    op.drop_index("ix_phase_results_attempt_id", table_name="phase_results")
    op.drop_table("phase_results")
    op.drop_index("ix_application_attempts_status", table_name="application_attempts")
    op.drop_index("ix_application_attempts_run_id", table_name="application_attempts")
    op.drop_index(
        "ix_application_attempts_application_id", table_name="application_attempts"
    )
    op.drop_table("application_attempts")
    op.drop_index("ix_application_jobs_status", table_name="application_jobs")
    op.drop_index("ix_application_jobs_source", table_name="application_jobs")
    op.drop_index("ix_application_jobs_platform", table_name="application_jobs")
    op.drop_table("application_jobs")
