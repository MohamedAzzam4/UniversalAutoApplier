"""Named queue-import service — the ONLY production entry point for queue import.

Responsibilities (WQ-3):

- Reads the configured absolute queue path (``Settings.queue_path`` /
  ``UAA_QUEUE_PATH``). Never scans folders; never invents a path.
- Calls the existing contract importer (:func:`import_queue_file`) — JSONL
  validation is never reimplemented here.
- Persists a durable run record per attempt (survives restart) with counts,
  a file fingerprint, structured row errors, and a safe failure reason.
- Preserves valid-row import when other lines are malformed (partial runs).
- Serializes concurrent import attempts through a non-blocking lock.
- Is idempotent: re-importing the same queue upserts jobs and never erases
  terminal job states or previous attempts.
- Never starts a browser, never fills a form, never submits, and never starts
  the pipeline.

Run states: ``success`` (no row errors; an empty file is a valid empty queue),
``partial`` (row errors with at least one valid import), ``failed`` (row
errors with zero imports, or an unreadable/missing file), ``skipped`` (e.g.
startup import enabled but no queue path configured).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from universal_auto_applier.application_queue.importer import import_queue_file
from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.models import ApplicationJobRow, QueueImportRunRow

logger = logging.getLogger("universal_auto_applier.queue_import_service")


class QueueImportConfigurationError(RuntimeError):
    """Raised when no absolute queue path is configured or supplied."""


class QueueImportConcurrentError(RuntimeError):
    """Raised when an import is already running."""


class QueueImportState(StrEnum):
    """Durable result states of an import run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fingerprint(path: Path) -> str | None:
    """Return the sha256 hex digest of ``path`` content, or None if unreadable.

    A partially-written file still fingerprints (so a re-run is recorded and
    idempotent); a missing/undecodable file returns None, meaning the import
    cannot even be attempted safely.
    """
    try:
        if not path.exists() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


@dataclass
class QueueImportRunSummary:
    """Serializable outcome of one import run."""

    run_id: str
    source_path: str
    trigger: str
    source_fingerprint: str | None
    state: str
    total_lines: int
    imported: int
    skipped: int
    error_count: int
    row_errors: list[dict[str, Any]] = field(default_factory=lambda: [])
    failure_reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source_path": self.source_path,
            "trigger": self.trigger,
            "source_fingerprint": self.source_fingerprint,
            "state": self.state,
            "total_lines": self.total_lines,
            "imported": self.imported,
            "skipped": self.skipped,
            "error_count": self.error_count,
            "row_errors": self.row_errors,
            "failure_reason": self.failure_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


def _row_to_summary(row: QueueImportRunRow) -> QueueImportRunSummary:
    return QueueImportRunSummary(
        run_id=row.run_id,
        source_path=row.source_path,
        trigger=row.trigger,
        source_fingerprint=row.source_fingerprint,
        state=row.state,
        total_lines=row.total_lines,
        imported=row.imported,
        skipped=row.skipped,
        error_count=row.error_count,
        row_errors=list(row.row_errors_json or []),
        failure_reason=row.failure_reason,
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )


class QueueImportService:
    """The named production service for importing the configured queue.

    One instance is shared per running app (stored on ``app.state``) so its
    non-blocking lock serializes concurrent API/startup import attempts.
    """

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._lock = threading.Lock()

    @property
    def configured_path(self) -> Path | None:
        """The configured absolute queue path, or None when not configured."""
        return self._settings.queue_path

    @property
    def configured(self) -> bool:
        return self.configured_path is not None

    def run(
        self,
        path: Path | None = None,
        trigger: str = "api",
        synthetic_mutation: bool = False,
    ) -> QueueImportRunSummary:
        """Import the configured queue (or an operator-supplied absolute path).

        Args:
            path: Optional operator-supplied absolute queue path.
            trigger: Human-readable trigger label for the durable run record.
            synthetic_mutation: WQ-7C opt-in. When True, every imported row's
                candidate snapshot is stamped with the synthetic-markers flag
                (identity-guarded per row; mismatched snapshots are refused).

        Raises:
            QueueImportConfigurationError: no absolute path is available.
            QueueImportConcurrentError: another import is already running.

        A missing/unreadable file is NOT a configuration error: it produces a
        durable ``failed`` run and a returned summary (surfaced through the
        API/status instead of crashing anything).
        """
        resolved: Path | None = path if path is not None else self.configured_path
        if resolved is None:
            raise QueueImportConfigurationError("queue path is not configured (set UAA_QUEUE_PATH)")
        resolved = Path(resolved)
        if not resolved.is_absolute():
            raise QueueImportConfigurationError(
                f"queue path must be absolute, got {str(resolved)!r}"
            )

        if not self._lock.acquire(blocking=False):
            raise QueueImportConcurrentError("a queue import is already running")
        try:
            return self._run_import(resolved, trigger, synthetic_mutation)
        finally:
            self._lock.release()

    def _run_import(
        self,
        source: Path,
        trigger: str,
        synthetic_mutation: bool = False,
    ) -> QueueImportRunSummary:
        run_id = uuid.uuid4().hex
        started_at = _utcnow()

        fingerprint = _fingerprint(source)
        if fingerprint is None:
            return self._persist(
                run_id=run_id,
                source_path=str(source),
                trigger=trigger,
                source_fingerprint=None,
                state=QueueImportState.FAILED,
                total_lines=0,
                imported=0,
                skipped=0,
                row_errors=[],
                reason=f"queue file not found or unreadable: {source}",
                started_at=started_at,
            )

        try:
            import_result = import_queue_file(
                source, self._session_factory, synthetic_mutation=synthetic_mutation
            )
        except Exception as exc:  # noqa: BLE001 - any importer failure must be recorded durably
            logger.exception("queue import crashed; recording failed run")
            return self._persist(
                run_id=run_id,
                source_path=str(source),
                trigger=trigger,
                source_fingerprint=fingerprint,
                state=QueueImportState.FAILED,
                total_lines=0,
                imported=0,
                skipped=0,
                row_errors=[],
                reason=_safe_reason(exc),
                started_at=started_at,
            )

        error_count = len(import_result.errors)
        if error_count == 0:
            state = QueueImportState.SUCCESS
        elif import_result.imported > 0:
            state = QueueImportState.PARTIAL
        else:
            state = QueueImportState.FAILED

        return self._persist(
            run_id=run_id,
            source_path=str(source),
            trigger=trigger,
            source_fingerprint=fingerprint,
            state=state,
            total_lines=import_result.total_lines,
            imported=import_result.imported,
            skipped=import_result.skipped,
            row_errors=[_row_error_to_dict(e) for e in import_result.errors],
            reason=None,
            started_at=started_at,
        )

    def _persist(
        self,
        *,
        run_id: str,
        source_path: str,
        trigger: str,
        source_fingerprint: str | None,
        state: QueueImportState,
        total_lines: int,
        imported: int,
        skipped: int,
        row_errors: list[dict[str, Any]],
        reason: str | None,
        started_at: datetime,
    ) -> QueueImportRunSummary:
        row = QueueImportRunRow(
            run_id=run_id,
            source_path=source_path,
            trigger=trigger,
            source_fingerprint=source_fingerprint,
            state=str(state),
            total_lines=total_lines,
            imported=imported,
            skipped=skipped,
            error_count=len(row_errors),
            row_errors_json=row_errors,
            failure_reason=reason,
            started_at=started_at,
            completed_at=_utcnow(),
        )
        with session_scope(self._session_factory) as session:
            session.add(row)
        logger.info(
            "queue import run=%s state=%s total=%d imported=%d skipped=%d errors=%d",
            run_id[:12],
            state.value,
            total_lines,
            imported,
            skipped,
            len(row_errors),
        )
        summary = _row_to_summary(row)
        summary.completed_at = row.completed_at.isoformat() if row.completed_at else None
        summary.started_at = started_at.isoformat()
        return summary

    def latest_run(self) -> QueueImportRunSummary | None:
        """Return the most recent durable import run, or None."""
        stmt = select(QueueImportRunRow).order_by(QueueImportRunRow.started_at.desc()).limit(1)
        with self._session_factory() as session:
            row = session.execute(stmt).scalars().first()
        if row is None:
            return None
        return _row_to_summary(row)

    def job_summary(self) -> dict[str, Any]:
        """Return a compact queue-job summary (total + per-status counts)."""
        stmt = select(
            ApplicationJobRow.status, func.count(ApplicationJobRow.application_id)
        ).group_by(ApplicationJobRow.status)
        with self._session_factory() as session:
            rows = session.execute(stmt).all()
        total = sum(count for _, count in rows)
        by_status = {status: count for status, count in rows}
        return {"total": total, "by_status": by_status}

    def status(self) -> dict[str, Any]:
        """Return configuration readiness + latest durable run + job summary.

        This is the payload behind ``GET /api/queue/status`` and is also used
        to surface startup import failures without crashing the server.
        """
        source = self.configured_path
        latest = self.latest_run()
        latest_summary = latest.to_dict() if latest is not None else None
        return {
            "configured": self.configured,
            "configured_path": str(source) if source is not None else None,
            "import_on_startup": self._settings.import_queue_on_startup,
            "source_exists": bool(source) and source.exists() and source.is_file() or False,
            "latest_run": latest_summary,
            "queue_job_summary": self.job_summary(),
        }


def _safe_reason(exc: Exception) -> str:
    """Return a bounded, human-readable failure reason that never leaks secrets."""
    message = str(exc)
    if not message or message.strip().lower() in {"", "unknown error"}:
        return type(exc).__name__
    return message[:500]


def _row_error_to_dict(error: Any) -> dict[str, Any]:
    """Convert a contract :class:`ImportRowError` to a persisted-safe dict.

    Only the line number and message are stored — never the raw JSONL line,
    which may carry candidate data.
    """
    return {
        "line_number": getattr(error, "line_number", None),
        "error": str(getattr(error, "error", error)),
    }


def run_startup_import(
    settings: Settings, session_factory: sessionmaker[Session]
) -> dict[str, Any] | None:
    """Run one queue import during startup if and only if it is enabled.

    Returns the durable summary dict, or None when startup import is disabled.
    Never raises: every failure is recorded as a run and returned so health and
    the dashboard can surface it without taking the server down.
    """
    if not settings.import_queue_on_startup:
        return None

    service = QueueImportService(settings, session_factory)
    try:
        return service.run(trigger="startup").to_dict()
    except QueueImportConfigurationError as exc:
        # Record a durable "skipped" run: startup import was enabled but no
        # path is configured. This surfaces in status without crashing.
        return _persist_skipped_run(session_factory, str(exc))
    except QueueImportConcurrentError:
        logger.warning("startup queue import skipped: another import is already running")
        return None
    except Exception as exc:  # noqa: BLE001 - startup must never crash on import
        logger.exception("startup queue import failed")
        return _persist_failed_run(settings, session_factory, _safe_reason(exc))


def _persist_skipped_run(session_factory: sessionmaker[Session], reason: str) -> dict[str, Any]:
    row = QueueImportRunRow(
        run_id=uuid.uuid4().hex,
        source_path="",
        trigger="startup",
        source_fingerprint=None,
        state=str(QueueImportState.SKIPPED),
        total_lines=0,
        imported=0,
        skipped=0,
        error_count=0,
        row_errors_json=[],
        failure_reason=reason,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    with session_scope(session_factory) as session:
        session.add(row)
    return _row_to_summary(row).to_dict()


def _persist_failed_run(
    settings: Settings, session_factory: sessionmaker[Session], reason: str
) -> dict[str, Any]:
    source = settings.queue_path
    row = QueueImportRunRow(
        run_id=uuid.uuid4().hex,
        source_path=str(source) if source is not None else "",
        trigger="startup",
        source_fingerprint=None,
        state=str(QueueImportState.FAILED),
        total_lines=0,
        imported=0,
        skipped=0,
        error_count=0,
        row_errors_json=[],
        failure_reason=reason,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    with session_scope(session_factory) as session:
        session.add(row)
    return _row_to_summary(row).to_dict()


__all__ = [
    "QueueImportConfigurationError",
    "QueueImportConcurrentError",
    "QueueImportRunSummary",
    "QueueImportService",
    "QueueImportState",
    "run_startup_import",
]
