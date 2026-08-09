"""Durable orchestration-run repository (WQ-6).

All orchestration run state mutations go through this repository — no other
module writes the ``orchestration_runs`` table directly. The orchestration
service creates/updates runs, and the API routes read them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.persistence.models import OrchestrationRunRow

# A run is "active" if it is in any of these states. Only one active run
# may exist at a time (enforced by the service layer's in-process lock).
ACTIVE_STATUSES = (
    "running",
    "jobhunter_running",
    "importing",
    "pipeline_running",
    "cancelling",
)
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_orchestration_run(
    session: Session,
    *,
    run_id: str,
    mode: str,
) -> OrchestrationRunRow:
    """Create a new durable orchestration run row and flush it."""
    row = OrchestrationRunRow(
        run_id=run_id,
        mode=mode,
        status="running",
        current_phase="initializing",
        last_action="Orchestration run created",
        last_error="",
        cancel_reason="",
        jobhunter_pid=None,
        jobhunter_started_at=None,
        jobhunter_finished_at=None,
        jobhunter_exit_code=None,
        jobhunter_stdout="",
        jobhunter_stderr="",
        queue_import_run_id=None,
        queue_import_state=None,
        queue_imported=0,
        queue_skipped=0,
        pipeline_run_id=None,
        pipeline_state=None,
        errors_json=[],
        started_at=_utcnow(),
        finished_at=None,
    )
    session.add(row)
    session.flush()
    return row


def get_orchestration_run(session: Session, run_id: str) -> OrchestrationRunRow | None:
    """Load a single run by id."""
    return session.get(OrchestrationRunRow, run_id)


def get_active_orchestration_run(session: Session) -> OrchestrationRunRow | None:
    """Return the active orchestration run, if any (most recent first)."""
    stmt = (
        select(OrchestrationRunRow)
        .where(OrchestrationRunRow.status.in_(ACTIVE_STATUSES))
        .order_by(OrchestrationRunRow.started_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def get_latest_orchestration_run(session: Session) -> OrchestrationRunRow | None:
    """Return the most recent orchestration run (any status)."""
    stmt = select(OrchestrationRunRow).order_by(OrchestrationRunRow.started_at.desc()).limit(1)
    return session.scalars(stmt).first()


def list_active_orchestration_runs(session: Session) -> list[OrchestrationRunRow]:
    """Return every active orchestration run, oldest first."""
    stmt = (
        select(OrchestrationRunRow)
        .where(OrchestrationRunRow.status.in_(ACTIVE_STATUSES))
        .order_by(OrchestrationRunRow.started_at.asc())
    )
    return list(session.scalars(stmt))


def update_orchestration_run(
    session: Session,
    run_id: str,
    **changes: Any,
) -> OrchestrationRunRow | None:
    """Apply partial field updates to an orchestration run row (single flush)."""
    row = session.get(OrchestrationRunRow, run_id)
    if row is None:
        return None
    for key, value in changes.items():
        if not hasattr(row, key):
            raise ValueError(f"orchestration run has no field {key!r}")
        setattr(row, key, value)
    session.flush()
    return row


def mark_orchestration_run_terminal(
    session: Session,
    run_id: str,
    *,
    status: str,
    last_action: str = "",
    last_error: str = "",
    cancel_reason: str = "",
) -> OrchestrationRunRow | None:
    """Transition a run to a terminal status and stamp the finish time."""
    return update_orchestration_run(
        session,
        run_id,
        status=status,
        finished_at=_utcnow(),
        last_action=last_action,
        last_error=last_error,
        cancel_reason=cancel_reason,
    )


def orchestration_run_to_dict(row: OrchestrationRunRow | None) -> dict[str, Any]:
    """Serialize an orchestration run row into the stable API state shape."""
    if row is None:
        return {
            "run_id": None,
            "mode": "sequential",
            "status": "idle",
            "current_phase": "",
            "last_action": "",
            "last_error": "",
            "cancel_reason": "",
            "jobhunter_pid": None,
            "jobhunter_started_at": None,
            "jobhunter_finished_at": None,
            "jobhunter_exit_code": None,
            "jobhunter_stdout": "",
            "jobhunter_stderr": "",
            "queue_import_run_id": None,
            "queue_import_state": None,
            "queue_imported": 0,
            "queue_skipped": 0,
            "pipeline_run_id": None,
            "pipeline_state": None,
            "errors": [],
            "started_at": None,
            "finished_at": None,
        }
    return {
        "run_id": row.run_id,
        "mode": row.mode,
        "status": row.status,
        "current_phase": row.current_phase,
        "last_action": row.last_action,
        "last_error": row.last_error,
        "cancel_reason": row.cancel_reason,
        "jobhunter_pid": row.jobhunter_pid,
        "jobhunter_started_at": row.started_at.isoformat() if row.started_at else None,
        "jobhunter_finished_at": row.jobhunter_finished_at.isoformat()
        if row.jobhunter_finished_at
        else None,
        "jobhunter_exit_code": row.jobhunter_exit_code,
        "jobhunter_stdout": row.jobhunter_stdout,
        "jobhunter_stderr": row.jobhunter_stderr,
        "queue_import_run_id": row.queue_import_run_id,
        "queue_import_state": row.queue_import_state,
        "queue_imported": row.queue_imported or 0,
        "queue_skipped": row.queue_skipped or 0,
        "pipeline_run_id": row.pipeline_run_id,
        "pipeline_state": row.pipeline_state,
        "errors": list(row.errors_json or []),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "create_orchestration_run",
    "get_orchestration_run",
    "get_active_orchestration_run",
    "get_latest_orchestration_run",
    "list_active_orchestration_runs",
    "update_orchestration_run",
    "mark_orchestration_run_terminal",
    "orchestration_run_to_dict",
]
