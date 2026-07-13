"""Logs and errors API.

Per ROADMAP WP 6.4: show latest logs, structured errors, and evidence links.

Phase 6: logs are kept in an in-memory ring buffer on app.state. In
production, a proper log handler would capture structured events. For now,
the API exposes whatever is available.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["logs"])

MAX_LOG_ENTRIES = 500


class LogEntry(BaseModel):
    """A single log entry."""

    timestamp: str
    level: str
    message: str
    application_id: str | None = None
    phase: str | None = None
    screenshot: str | None = None


class LogsResponse(BaseModel):
    """Response for the logs endpoint."""

    total: int
    entries: list[LogEntry]


@router.get("/logs", response_model=LogsResponse)
def get_logs(request: Request, limit: int = 50) -> LogsResponse:
    """Return recent log entries.

    Phase 6: reads from an in-memory ring buffer on app.state.log_buffer.
    If no buffer exists, returns an empty list.
    """
    app = request.app
    buffer: deque[dict[str, Any]] | None = getattr(app.state, "log_buffer", None)

    if buffer is None:
        return LogsResponse(total=0, entries=[])

    entries = list(buffer)[-limit:]
    return LogsResponse(
        total=len(buffer),
        entries=[
            LogEntry(
                timestamp=e.get("timestamp", ""),
                level=e.get("level", "info"),
                message=e.get("message", ""),
                application_id=e.get("application_id"),
                phase=e.get("phase"),
                screenshot=e.get("screenshot"),
            )
            for e in entries
        ],
    )


@router.get("/errors", response_model=LogsResponse)
def get_errors(request: Request, limit: int = 50) -> LogsResponse:
    """Return recent error-level log entries only."""
    app = request.app
    buffer: deque[dict[str, Any]] | None = getattr(app.state, "log_buffer", None)

    if buffer is None:
        return LogsResponse(total=0, entries=[])

    entries = [e for e in buffer if e.get("level") in ("error", "warning")][-limit:]
    return LogsResponse(
        total=len(entries),
        entries=[
            LogEntry(
                timestamp=e.get("timestamp", ""),
                level=e.get("level", "info"),
                message=e.get("message", ""),
                application_id=e.get("application_id"),
                phase=e.get("phase"),
                screenshot=e.get("screenshot"),
            )
            for e in entries
        ],
    )


def init_log_buffer(app: Any) -> None:
    """Initialize the in-memory log buffer on app.state."""
    if not hasattr(app.state, "log_buffer"):
        app.state.log_buffer = deque[dict[str, Any]](maxlen=MAX_LOG_ENTRIES)


def add_log_entry(
    app: Any,
    *,
    level: str = "info",
    message: str,
    application_id: str | None = None,
    phase: str | None = None,
    screenshot: str | None = None,
) -> None:
    """Add a log entry to the in-memory buffer."""
    from datetime import UTC, datetime

    buffer: deque[dict[str, Any]] | None = getattr(app.state, "log_buffer", None)
    if buffer is None:
        return

    buffer.append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            "application_id": application_id,
            "phase": phase,
            "screenshot": screenshot,
        }
    )
