"""Stale pipeline-run recovery (WQ-5).

Runs exactly once at app startup (after migrations, before any pipeline
action is accepted). For every run that is still in an active status
(running / pausing / paused / cancelling) it proves staleness from the
durable worker-liveness fields:

- the run's worker pid is missing or no longer running, AND
- its heartbeat is missing or older than ``settings.pipeline_heartbeat_timeout_ms``.

A run owned by a live process (or whose heartbeat is still fresh) is NEVER
touched — a healthy worker keeps its run even across an API restart.

Recovered runs are marked terminal ``recovered`` (durable reason in
``last_action``/``last_error``, ``finished_at`` stamped, run id / counters /
errors / current-job context preserved). If the interrupted run had a job
still ``in_progress``, that job becomes ``needs_review`` through the job
store guard and receives exactly one idempotent intervention explaining the
interruption and the next safe action. Nothing is auto-resubmitted, terminal
jobs are never downgraded, and a recovered run never blocks a fresh start.

Safety: this module never performs browser work, never calls an adapter, and
never touches submission tables.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from universal_auto_applier.config import Settings
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import update_application_status
from universal_auto_applier.persistence.models import ApplicationJobRow, PipelineRunRow
from universal_auto_applier.persistence.pipeline_run_repository import (
    list_active_pipeline_runs,
    update_pipeline_run,
)

logger = logging.getLogger("universal_auto_applier.pipeline_recovery_service")

RECOVERY_QUESTION = (
    "The pipeline worker that was processing this job was interrupted (its "
    "run was recovered on restart). The job was paused mid-flight and "
    "nothing was submitted. Review the current form state and continue "
    "manually; the pipeline will not repeat this job automatically."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _pid_is_alive(pid: int) -> bool:
    """Return True when ``pid`` still exists (or cannot be proven dead).

    On Windows, ``os.kill(pid, 0)`` does not raise for a nonexistent pid, so
    the POSIX trick alone is unreliable; verify through the process API when
    the signal probe "succeeds".
    """
    if os.name == "nt":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user: treat as alive.
        return True
    return True


# Public alias for cross-module use (e.g. orchestration recovery).
pid_is_alive = _pid_is_alive


def _pid_is_alive_windows(pid: int) -> bool:
    """Windows liveness probe via OpenProcess (query-only access).

    ``ctypes.windll`` only exists on Windows; resolve it dynamically so the
    module type-checks on every platform.
    """
    import ctypes

    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _heartbeat_expired(heartbeat: datetime | None, timeout: timedelta) -> bool:
    """Return True when the heartbeat is missing or older than ``timeout``."""
    if heartbeat is None:
        return True
    # SQLite returns naive datetimes; normalize both sides to UTC.
    hb = heartbeat
    if hb.tzinfo is not None:
        hb = hb.astimezone(UTC)
    else:
        hb = hb.replace(tzinfo=UTC)
    return _utcnow() - hb > timeout


def run_is_stale(row: PipelineRunRow, timeout: timedelta) -> bool:
    """Prove staleness: dead/missing worker pid AND expired/missing heartbeat.

    A run with a live worker pid or a fresh heartbeat is never stale — the
    worker owns it and recovery must not take it over.
    """
    if row.worker_pid is not None and _pid_is_alive(row.worker_pid):
        return False
    return _heartbeat_expired(row.heartbeat_at, timeout)


def _recover_current_job(
    session: Any,
    application_id: str | None,
    run_id: str,
) -> None:
    """Move one interrupted in-progress job to ``needs_review``.

    Only jobs still ``in_progress`` are touched, and only through the job
    store's transition guard. Terminal jobs and untouched jobs are never
    altered. Exactly one idempotent intervention explains the interruption.
    """
    if not application_id:
        return
    job = session.get(ApplicationJobRow, application_id)
    if job is None:
        return
    if str(job.status) != str(ApplicationStatus.IN_PROGRESS):
        return
    try:
        update_application_status(session, application_id, ApplicationStatus.NEEDS_REVIEW)
    except ValueError:
        logger.warning(
            "recovery: job %s is %s; refusing the needs_review transition",
            application_id[:12],
            job.status,
        )
        return
    create_intervention(
        session,
        application_id=application_id,
        kind=InterventionKind.RECOVERY,
        question=RECOVERY_QUESTION,
        field_selector="",
    )
    logger.info(
        "recovery: job %s moved to needs_review (run %s)",
        application_id[:12],
        run_id[:8],
    )


def _recover_run(session: Any, row: PipelineRunRow) -> None:
    """Mark one stale run terminal with a durable reason; recover its job."""
    now = _utcnow()
    pid = row.worker_pid
    heartbeat = row.heartbeat_at.isoformat() if row.heartbeat_at else "missing"
    reason = (
        f"Pipeline run {row.run_id[:8]} was interrupted: worker "
        f"(pid={pid if pid is not None else 'missing'}) is not running and the "
        f"last heartbeat was {heartbeat}. The run was recovered to a safe "
        f"terminal state; nothing was submitted and nothing will auto-retry. "
        f"Review the current job before starting a new run."
    )
    # Preserve current_job_id / current_phase and the run's counters and
    # error history: only the status/finish fields are terminal-stamped.
    update_pipeline_run(
        session,
        row.run_id,
        status="recovered",
        finished_at=now,
        last_action="Recovered after interruption; review the current job before resuming",
        last_error=reason,
    )
    _recover_current_job(session, row.current_job_id, row.run_id)


def recover_stale_pipeline_runs(session_factory: Any, settings: Settings) -> dict[str, Any]:
    """Scan active runs and recover the proven-stale ones (startup only).

    Idempotent: recovered runs are terminal, so a second invocation (or a
    second restart) finds nothing to do. Returns a summary dict for logging
    and tests.
    """
    timeout = timedelta(milliseconds=settings.pipeline_heartbeat_timeout_ms)
    recovered: list[str] = []
    healthy_kept: list[str] = []
    with session_scope(session_factory) as session:
        for row in list_active_pipeline_runs(session):
            if run_is_stale(row, timeout):
                _recover_run(session, row)
                recovered.append(row.run_id)
            else:
                healthy_kept.append(row.run_id)
    if recovered:
        logger.warning(
            "startup recovery: recovered stale run(s): %s",
            [run_id[:8] for run_id in recovered],
        )
    return {
        "recovered": recovered,
        "healthy_kept": healthy_kept,
    }


__all__ = ["recover_stale_pipeline_runs", "run_is_stale", "pid_is_alive", "_pid_is_alive"]
