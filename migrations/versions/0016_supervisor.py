"""Add AI-supervisor (agent-assisted operator mode V0) tables.

Revision ID: 0016_supervisor
Revises: 0015_submission_authorization
Create Date: 2026-09-01 00:00:00

Adds the durable supervisor state:

- ``supervisor_runs`` — one bounded review-only supervisor run.
- ``supervisor_application_states`` — latest supervisor state per application.
- ``supervisor_events`` — per-action audit log ("why did the agent do this?").
- ``human_handoffs`` — structured tasks for the human owner.
- ``repair_tickets`` — sanitized implementation-defect reports.

The supervisor has no submission capability; none of these tables carry any
authorization, approval, or submit state. Sensitive answer values are never
persisted in supervisor rows (redacted metadata only).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_supervisor"
down_revision: Union[str, None] = "0015_submission_authorization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supervisor_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("queue_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("summary_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_supervisor_runs_status", "supervisor_runs", ["status"])

    op.create_table(
        "supervisor_application_states",
        sa.Column("application_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("decision_source", sa.String(32), nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_supervisor_application_states_run_id",
        "supervisor_application_states",
        ["run_id"],
    )
    op.create_index(
        "ix_supervisor_application_states_state",
        "supervisor_application_states",
        ["state"],
    )

    op.create_table(
        "supervisor_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(64), nullable=False),
        sa.Column("previous_state", sa.String(32), nullable=False, server_default=""),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("resulting_state", sa.String(32), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("decision_source", sa.String(32), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_supervisor_events_run_id", "supervisor_events", ["run_id"])
    op.create_index(
        "ix_supervisor_events_application_id",
        "supervisor_events",
        ["application_id"],
    )

    op.create_table(
        "human_handoffs",
        sa.Column("handoff_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(64), nullable=False),
        sa.Column("company", sa.String(256), nullable=False, server_default=""),
        sa.Column("role", sa.String(256), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("action_required", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_human_handoffs_run_id", "human_handoffs", ["run_id"])
    op.create_index("ix_human_handoffs_application_id", "human_handoffs", ["application_id"])
    op.create_index("ix_human_handoffs_reason_code", "human_handoffs", ["reason_code"])
    op.create_index("ix_human_handoffs_status", "human_handoffs", ["status"])

    op.create_table(
        "repair_tickets",
        sa.Column("ticket_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("application_id", sa.String(64), nullable=False),
        sa.Column("ats_family", sa.String(64), nullable=False, server_default=""),
        sa.Column("page_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("field_label", sa.String(256), nullable=False, server_default=""),
        sa.Column("field_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("expected_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("actual_failure", sa.Text(), nullable=False, server_default=""),
        sa.Column("selector_metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("retry_history_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("suggested_reproduction", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_repair_tickets_run_id", "repair_tickets", ["run_id"])
    op.create_index("ix_repair_tickets_application_id", "repair_tickets", ["application_id"])
    op.create_index("ix_repair_tickets_reason_code", "repair_tickets", ["reason_code"])
    op.create_index("ix_repair_tickets_status", "repair_tickets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_repair_tickets_status", table_name="repair_tickets")
    op.drop_index("ix_repair_tickets_reason_code", table_name="repair_tickets")
    op.drop_index("ix_repair_tickets_application_id", table_name="repair_tickets")
    op.drop_index("ix_repair_tickets_run_id", table_name="repair_tickets")
    op.drop_table("repair_tickets")
    op.drop_index("ix_human_handoffs_status", table_name="human_handoffs")
    op.drop_index("ix_human_handoffs_reason_code", table_name="human_handoffs")
    op.drop_index("ix_human_handoffs_application_id", table_name="human_handoffs")
    op.drop_index("ix_human_handoffs_run_id", table_name="human_handoffs")
    op.drop_table("human_handoffs")
    op.drop_index(
        "ix_supervisor_events_application_id",
        table_name="supervisor_events",
    )
    op.drop_index("ix_supervisor_events_run_id", table_name="supervisor_events")
    op.drop_table("supervisor_events")
    op.drop_index(
        "ix_supervisor_application_states_state",
        table_name="supervisor_application_states",
    )
    op.drop_index(
        "ix_supervisor_application_states_run_id",
        table_name="supervisor_application_states",
    )
    op.drop_table("supervisor_application_states")
    op.drop_index("ix_supervisor_runs_status", table_name="supervisor_runs")
    op.drop_table("supervisor_runs")
