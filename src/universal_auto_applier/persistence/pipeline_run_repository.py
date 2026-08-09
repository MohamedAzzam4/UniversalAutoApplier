"""Durable pipeline-run repository (WQ-4).

All pipeline run state mutations go through this repository — no other
module writes the ``pipeline_runs`` table directly. The worker service
creates/updates runs, the API routes read them, and the worker subprocess
reports progress into the same row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.persistence.models import PipelineRunRow

ACTIVE_STATUSES = ("running", "pausing", "paused", "cancelling")
# WQ-5: "recovered" is a terminal outcome written by stale-run recovery on
# startup. It leaves the run out of ACTIVE_STATUSES so a fresh start is
# allowed afterwards while a healthy active run still blocks with 409.
TERMINAL_STATUSES = ("cancelled", "completed", "failed", "recovered")


def create_pipeline_run(
    session: Session,
    *,
    run_id: str,
    status: str = "running",
    mode: str = "sequential_dry_run",
) -> PipelineRunRow:
    """Create a new durable pipeline run row and flush it."""
    row = PipelineRunRow(
        run_id=run_id,
        status=status,
        mode=mode,
        current_job_id=None,
        current_phase="",
        last_action="",
        last_error="",
        cancel_reason="",
        jobs_total=0,
        jobs_completed=0,
        jobs_failed=0,
        jobs_skipped=0,
        errors_json=[],
        started_at=datetime.now(UTC),
        finished_at=None,
    )
    session.add(row)
    session.flush()
    return row


def get_pipeline_run(session: Session, run_id: str) -> PipelineRunRow | None:
    """Load a single run by id."""
    return session.get(PipelineRunRow, run_id)


def list_pipeline_runs(session: Session, *, limit: int = 20) -> list[PipelineRunRow]:
    """Return the most recent runs, newest first."""
    stmt = select(PipelineRunRow).order_by(PipelineRunRow.started_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def get_active_pipeline_run(session: Session) -> PipelineRunRow | None:
    """Return the first run that is still in progress, if any."""
    stmt = (
        select(PipelineRunRow)
        .where(PipelineRunRow.status.in_(ACTIVE_STATUSES))
        .order_by(PipelineRunRow.started_at.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def list_active_pipeline_runs(session: Session) -> list[PipelineRunRow]:
    """Return every run still in progress, oldest first.

    Used by startup stale-run recovery (WQ-5) to scan all unfinished runs.
    """
    stmt = (
        select(PipelineRunRow)
        .where(PipelineRunRow.status.in_(ACTIVE_STATUSES))
        .order_by(PipelineRunRow.started_at.asc())
    )
    return list(session.scalars(stmt))


def get_latest_pipeline_run(session: Session) -> PipelineRunRow | None:
    """Return the most recent run (any status), newest first."""
    stmt = select(PipelineRunRow).order_by(PipelineRunRow.started_at.desc()).limit(1)
    return session.scalars(stmt).first()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def update_pipeline_run(
    session: Session,
    run_id: str,
    **changes: Any,
) -> PipelineRunRow | None:
    """Apply partial field updates to a run row (single flush)."""
    row = session.get(PipelineRunRow, run_id)
    if row is None:
        return None
    for key, value in changes.items():
        if not hasattr(row, key):
            raise ValueError(f"pipeline run has no field {key!r}")
        setattr(row, key, value)
    session.flush()
    return row


def mark_pipeline_run_terminal(
    session: Session,
    run_id: str,
    *,
    status: str,
    finished_at: datetime | None = None,
    last_action: str = "",
    last_error: str = "",
) -> PipelineRunRow | None:
    """Transition a run to a terminal status and stamp the finish time."""
    return update_pipeline_run(
        session,
        run_id,
        status=status,
        finished_at=finished_at if finished_at is not None else _utcnow(),
        last_action=last_action,
        last_error=last_error,
        current_job_id=None,
        current_phase="",
    )


def pipeline_run_to_dict(row: PipelineRunRow | None) -> dict[str, Any]:
    """Serialize a run row into the stable API state shape."""
    if row is None:
        return {
            "run_id": None,
            "status": "idle",
            "mode": "sequential_dry_run",
            "current_job_id": None,
            "current_phase": "",
            "last_action": "",
            "last_error": "",
            "jobs_total": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_skipped": 0,
            "started_at": None,
            "finished_at": None,
            "cancel_reason": "",
            "errors": [],
        }
    return {
        "run_id": row.run_id,
        "status": row.status,
        "mode": row.mode,
        "current_job_id": row.current_job_id,
        "current_phase": row.current_phase,
        "last_action": row.last_action,
        "last_error": row.last_error,
        "jobs_total": row.jobs_total,
        "jobs_completed": row.jobs_completed,
        "jobs_failed": row.jobs_failed,
        "jobs_skipped": row.jobs_skipped,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "cancel_reason": row.cancel_reason,
        "errors": list(row.errors_json or []),
    }


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "create_pipeline_run",
    "get_pipeline_run",
    "list_pipeline_runs",
    "get_active_pipeline_run",
    "list_active_pipeline_runs",
    "get_latest_pipeline_run",
    "update_pipeline_run",
    "mark_pipeline_run_terminal",
    "pipeline_run_to_dict",
]
