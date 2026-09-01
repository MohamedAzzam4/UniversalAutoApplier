"""Persistence for supervisor runs, states, events, handoffs, repair tickets.

All supervisor mutations go through these store functions (state-through-
store doctrine). Sessions are owned by the caller — no function here
commits; use ``session_scope`` or the service's transaction boundary.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.persistence.models import (
    HumanHandoffRow,
    RepairTicketRow,
    SupervisorApplicationStateRow,
    SupervisorEventRow,
    SupervisorRunRow,
)


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def create_supervisor_run(session: Session, *, queue_path: str = "") -> SupervisorRunRow:
    row = SupervisorRunRow(
        run_id=_new_id(),
        status="running",
        queue_path=queue_path,
        review_only=True,
        summary_json={},
        started_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def finish_supervisor_run(
    session: Session,
    run_id: str,
    *,
    status: str,
    summary: dict[str, Any],
    error_message: str | None = None,
) -> SupervisorRunRow | None:
    row = session.get(SupervisorRunRow, run_id)
    if row is None:
        return None
    row.status = status
    row.summary_json = summary
    row.error_message = error_message
    row.finished_at = _utcnow()
    session.flush()
    return row


def get_supervisor_run(session: Session, run_id: str) -> SupervisorRunRow | None:
    return session.get(SupervisorRunRow, run_id)


def list_supervisor_runs(session: Session, *, limit: int = 20) -> list[SupervisorRunRow]:
    stmt = (
        select(SupervisorRunRow)
        .order_by(SupervisorRunRow.started_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Per-application state
# ---------------------------------------------------------------------------


def set_supervisor_application_state(
    session: Session,
    *,
    application_id: str,
    run_id: str,
    state: str,
    reason_code: str = "",
    decision_source: str = "",
    retry_count: int = 0,
    detail_json: dict[str, Any] | None = None,
) -> SupervisorApplicationStateRow:
    row = session.get(SupervisorApplicationStateRow, application_id)
    if row is None:
        row = SupervisorApplicationStateRow(
            application_id=application_id,
            run_id=run_id,
            state=state,
            reason_code=reason_code,
            decision_source=decision_source,
            retry_count=retry_count,
            detail_json=detail_json or {},
            updated_at=_utcnow(),
        )
        session.add(row)
    else:
        row.run_id = run_id
        row.state = state
        row.reason_code = reason_code
        row.decision_source = decision_source
        row.retry_count = retry_count
        row.detail_json = detail_json or {}
        row.updated_at = _utcnow()
    session.flush()
    return row


def get_supervisor_application_state(
    session: Session, application_id: str
) -> SupervisorApplicationStateRow | None:
    return session.get(SupervisorApplicationStateRow, application_id)


def list_supervisor_application_states(
    session: Session, *, run_id: str | None = None
) -> list[SupervisorApplicationStateRow]:
    stmt = select(SupervisorApplicationStateRow).order_by(
        SupervisorApplicationStateRow.application_id  # type: ignore[union-attr]
    )
    if run_id is not None:
        stmt = stmt.where(SupervisorApplicationStateRow.run_id == run_id)  # type: ignore[union-attr]
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


def record_supervisor_event(
    session: Session,
    *,
    run_id: str,
    application_id: str,
    action: str,
    previous_state: str = "",
    resulting_state: str = "",
    reason_code: str = "",
    decision_source: str = "",
    confidence: float | None = None,
    retry_count: int = 0,
    tool_result: str = "",
    detail_json: dict[str, Any] | None = None,
) -> SupervisorEventRow:
    row = SupervisorEventRow(
        event_id=_new_id(),
        run_id=run_id,
        application_id=application_id,
        previous_state=previous_state,
        action=action,
        resulting_state=resulting_state,
        reason_code=reason_code,
        decision_source=decision_source,
        confidence=confidence,
        retry_count=retry_count,
        tool_result=tool_result,
        detail_json=detail_json or {},
        created_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def list_supervisor_events(
    session: Session,
    *,
    run_id: str | None = None,
    application_id: str | None = None,
    limit: int = 200,
) -> list[SupervisorEventRow]:
    stmt = (
        select(SupervisorEventRow)
        .order_by(SupervisorEventRow.created_at.asc())  # type: ignore[union-attr]
        .limit(limit)
    )
    if run_id is not None:
        stmt = stmt.where(SupervisorEventRow.run_id == run_id)  # type: ignore[union-attr]
    if application_id is not None:
        stmt = stmt.where(SupervisorEventRow.application_id == application_id)  # type: ignore[union-attr]
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Human handoffs
# ---------------------------------------------------------------------------


def create_human_handoff(
    session: Session,
    *,
    run_id: str,
    application_id: str,
    reason_code: str,
    company: str = "",
    role: str = "",
    question: str = "",
    action_required: str = "",
    detail_json: dict[str, Any] | None = None,
) -> HumanHandoffRow:
    row = HumanHandoffRow(
        handoff_id=_new_id(),
        run_id=run_id,
        application_id=application_id,
        company=company,
        role=role,
        reason_code=reason_code,
        question=question,
        action_required=action_required,
        detail_json=detail_json or {},
        status="open",
        created_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def list_human_handoffs(
    session: Session,
    *,
    run_id: str | None = None,
    status: str | None = None,
) -> list[HumanHandoffRow]:
    stmt = select(HumanHandoffRow).order_by(HumanHandoffRow.created_at.asc())  # type: ignore[union-attr]
    if run_id is not None:
        stmt = stmt.where(HumanHandoffRow.run_id == run_id)  # type: ignore[union-attr]
    if status is not None:
        stmt = stmt.where(HumanHandoffRow.status == status)  # type: ignore[union-attr]
    return list(session.scalars(stmt))


def resolve_human_handoff(session: Session, handoff_id: str) -> HumanHandoffRow | None:
    row = session.get(HumanHandoffRow, handoff_id)
    if row is None:
        return None
    row.status = "resolved"
    row.resolved_at = _utcnow()
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Repair tickets
# ---------------------------------------------------------------------------


def create_repair_ticket(
    session: Session,
    *,
    run_id: str,
    application_id: str,
    reason_code: str,
    ats_family: str = "",
    page_fingerprint: str = "",
    field_label: str = "",
    field_type: str = "",
    expected_source: str = "",
    actual_failure: str = "",
    selector_metadata: dict[str, Any] | None = None,
    retry_history: list[dict[str, Any]] | None = None,
    suggested_reproduction: str = "",
) -> RepairTicketRow:
    row = RepairTicketRow(
        ticket_id=_new_id(),
        run_id=run_id,
        application_id=application_id,
        ats_family=ats_family,
        page_fingerprint=page_fingerprint,
        field_label=field_label,
        field_type=field_type,
        reason_code=reason_code,
        expected_source=expected_source,
        actual_failure=actual_failure,
        selector_metadata_json=selector_metadata or {},
        retry_history_json=retry_history or [],
        suggested_reproduction=suggested_reproduction,
        status="open",
        created_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def list_repair_tickets(
    session: Session,
    *,
    run_id: str | None = None,
    status: str | None = None,
) -> list[RepairTicketRow]:
    stmt = select(RepairTicketRow).order_by(RepairTicketRow.created_at.asc())  # type: ignore[union-attr]
    if run_id is not None:
        stmt = stmt.where(RepairTicketRow.run_id == run_id)  # type: ignore[union-attr]
    if status is not None:
        stmt = stmt.where(RepairTicketRow.status == status)  # type: ignore[union-attr]
    return list(session.scalars(stmt))


__all__ = [
    "create_human_handoff",
    "create_repair_ticket",
    "create_supervisor_run",
    "finish_supervisor_run",
    "get_supervisor_application_state",
    "get_supervisor_run",
    "list_human_handoffs",
    "list_repair_tickets",
    "list_supervisor_application_states",
    "list_supervisor_events",
    "list_supervisor_runs",
    "record_supervisor_event",
    "resolve_human_handoff",
    "set_supervisor_application_state",
]
