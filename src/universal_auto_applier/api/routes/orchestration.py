"""Cross-repository orchestration API (WQ-6).

Endpoints:
- POST /api/orchestration/start — start an orchestration run (sequential/parallel)
- POST /api/orchestration/cancel — request cancellation
- GET  /api/orchestration/status — current orchestration state (durable, pollable)

The orchestration service coordinates JobHunter export -> UAA queue import ->
UAA pipeline. It never performs final submission and never imports JobHunter
Python modules. Only one active orchestration run may exist at a time (409).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["orchestration"])

_SAFETY_MESSAGE = (
    "Cross-repository orchestration. JobHunter runs as an external subprocess; "
    "UAA imports the queue and starts the dry-run pipeline. No final submissions."
)


class OrchestrationStartRequest(BaseModel):
    """Request to start an orchestration run."""

    mode: str | None = Field(
        default=None,
        description=(
            "Orchestration mode: 'sequential' (JobHunter completes before UAA "
            "import+pipeline) or 'parallel' (UAA pipeline starts for existing "
            "jobs while JobHunter runs concurrently). Defaults to "
            "UAA_ORCHESTRATION_MODE."
        ),
    )
    fixture_html: str | None = Field(
        default=None,
        description=(
            "Optional test-only fixture HTML for the UAA pipeline dry-run path. Never submitted."
        ),
    )
    max_jobs: int = Field(default=10, ge=1, le=100)


def _require_service(request: Request) -> Any:
    service = getattr(request.app.state, "orchestration_service", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Orchestration service not initialized",
        )
    return service


@router.post("/orchestration/start")
def start_orchestration(request: Request, body: OrchestrationStartRequest) -> dict[str, Any]:
    """Start a cross-repository orchestration run.

    Returns immediately with the durable run state. The run executes in a
    background thread; poll ``GET /api/orchestration/status`` for progress.

    Safety:
    - Only one active orchestration run at a time (409 if already running).
    - Never performs final submission.
    - JobHunter runs as an external subprocess; UAA never imports its modules.
    """
    service = _require_service(request)
    try:
        state = service.start(
            mode=body.mode,
            fixture_html=body.fixture_html,
            max_jobs=body.max_jobs,
        )
    except Exception as exc:
        # Distinguish configuration errors (400) from concurrent errors (409).
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConcurrentError,
            OrchestrationConfigurationError,
        )

        if isinstance(exc, OrchestrationConcurrentError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, OrchestrationConfigurationError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    state["message"] = _SAFETY_MESSAGE
    return state


@router.post("/orchestration/cancel")
def cancel_orchestration(request: Request) -> dict[str, Any]:
    """Request cancellation of the active orchestration run.

    - Requests UAA pipeline cancellation safely.
    - Terminates only the owned JobHunter child process (graceful first).
    - Persists the final cancellation outcome.
    """
    service = _require_service(request)
    state = service.cancel()
    state["message"] = _SAFETY_MESSAGE
    return state


@router.get("/orchestration/status")
def get_orchestration_status(request: Request) -> dict[str, Any]:
    """Return the current orchestration state.

    Safe to poll repeatedly. State is durable — it survives API/server
    restarts and the run_id/phase/counts persist in SQLite.
    """
    try:
        service = _require_service(request)
    except HTTPException:
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
            "pipeline_run_id_initial": None,
            "pipeline_state_initial": None,
            "pipeline_run_id": None,
            "pipeline_state": None,
            "queue_hash_before": None,
            "queue_hash_after": None,
            "queue_mtime_ns_before": None,
            "queue_mtime_ns_after": None,
            "queue_published": None,
            "newly_eligible_count": 0,
            "newly_eligible_ids": [],
            "targeted_ids": [],
            "processed_ids": [],
            "remaining_ids": [],
            "targeted_count": 0,
            "processed_count": 0,
            "remaining_count": 0,
            "pipeline_run_ids": [],
            "pass_count": 0,
            "errors": [],
            "started_at": None,
            "finished_at": None,
            "jobhunter_workers": 1,
            "pipeline_workers": 1,
            "max_jobs": "batch-size limit (not worker count)",
            "message": "Orchestration service not initialized",
        }
    state = service.status()
    state["message"] = _SAFETY_MESSAGE
    state["jobhunter_workers"] = 1
    state["pipeline_workers"] = 1
    state["max_jobs"] = "batch-size limit (not worker count)"
    return state
