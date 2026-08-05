"""Queue import and status API (WQ-3).

    POST /api/queue/import  - import ONLY the configured queue path.
    GET  /api/queue/status  - configuration readiness, latest durable run,
                              counts, errors, fingerprint, queue-job summary.

The import endpoint never accepts a browser-supplied file path: it reads the
configured ``UAA_QUEUE_PATH`` (or the legacy ``UAA_JOBHUNTER_QUEUE``) through
the named :class:`QueueImportService`. HTTP errors are used only for missing
configuration (400) and concurrent import attempts (409); a missing file is a
durable ``failed`` run returned in the 200 payload so it stays visible in the
dashboard and in history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from universal_auto_applier.services.queue_import_service import (
    QueueImportConcurrentError,
    QueueImportConfigurationError,
    QueueImportService,
)

router = APIRouter(tags=["queue-import"])


class QueueRowError(BaseModel):
    """One structured row-level import error (line number + message only)."""

    line_number: int | None = None
    error: str = ""


class QueueImportRun(BaseModel):
    """A durable queue-import run summary."""

    run_id: str
    source_path: str
    trigger: str
    source_fingerprint: str | None = None
    state: str
    total_lines: int
    imported: int
    skipped: int
    error_count: int
    row_errors: list[QueueRowError] = []
    failure_reason: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class QueueImportStartResponse(BaseModel):
    """Response of ``POST /api/queue/import``."""

    run: QueueImportRun


class QueueStatusResponse(BaseModel):
    """Response of ``GET /api/queue/status``."""

    configured: bool
    configured_path: str | None = None
    import_on_startup: bool
    source_exists: bool = False
    latest_run: QueueImportRun | None = None
    queue_job_summary: dict[str, Any]


def _get_service(request: Request) -> QueueImportService:
    """Return the app-scoped :class:`QueueImportService` (created on first use).

    Storing the instance on ``app.state`` shares its concurrency lock across
    requests, so concurrent import attempts are reliably rejected.
    """
    service: QueueImportService | None = getattr(request.app.state, "queue_import_service", None)
    if service is None:
        service = QueueImportService(request.app.state.settings, request.app.state.session_factory)
        request.app.state.queue_import_service = service
    return service


@router.post("/queue/import", response_model=QueueImportStartResponse)
def import_queue(request: Request) -> QueueImportStartResponse:
    """Import the configured queue file once.

    Imports ONLY ``UAA_QUEUE_PATH``. Returns the durable run summary; a
    missing/unreadable file is a persisted ``failed`` run in the 200 payload.
    """
    service = _get_service(request)
    try:
        summary = service.run(trigger="api")
    except QueueImportConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QueueImportConcurrentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return QueueImportStartResponse(run=QueueImportRun(**summary.to_dict()))


@router.get("/queue/status", response_model=QueueStatusResponse)
def queue_status(request: Request) -> QueueStatusResponse:
    """Return queue configuration readiness and the latest durable import run."""
    service = _get_service(request)
    status = service.status()
    latest = status.get("latest_run")
    return QueueStatusResponse(
        configured=status["configured"],
        configured_path=status["configured_path"],
        import_on_startup=status["import_on_startup"],
        source_exists=status["source_exists"],
        latest_run=QueueImportRun(**latest) if latest is not None else None,
        queue_job_summary=status["queue_job_summary"],
    )


__all__ = [
    "QueueImportRun",
    "QueueImportStartResponse",
    "QueueStatusResponse",
    "router",
]
