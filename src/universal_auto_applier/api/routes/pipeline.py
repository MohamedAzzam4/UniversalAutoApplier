"""Pipeline start/control API.

WQ-4: Background sequential browser pipeline with dashboard controls.

Endpoints:
- POST /api/pipeline/start — start background pipeline (returns immediately)
- POST /api/pipeline/pause — request pause
- POST /api/pipeline/resume — resume paused pipeline
- POST /api/pipeline/cancel — request cancellation
- GET  /api/pipeline/status — current pipeline state (durable, pollable)

The pipeline runs in a dedicated worker subprocess (never in a request
thread). Every response is a stable :class:`PipelineRunState` model. The
pipeline never performs final submission — only dry-run/review behavior.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["pipeline"])

_SAFETY_MESSAGE = "Background dry-run pipeline. No final submissions occur from this pipeline."


class PipelineStartRequest(BaseModel):
    """Request to start the pipeline.

    ``fixture_html`` is honored — it puts the run in deterministic fixture
    mode (generic orchestrator dry-run against the given HTML, no browser,
    no network). This is a test/CI-only convenience and is documented as
    such in the OpenAPI description. ``None`` (the default) runs the live
    browser dry-run path.
    """

    fixture_html: str | None = Field(
        default=None,
        description=(
            "Optional test-only fixture HTML. When set, the run processes "
            "jobs against this local HTML via the generic dry-run path "
            "(no browser, no network). When omitted, the live browser "
            "dry-run path is used. Never submits in either mode."
        ),
    )
    max_jobs: int = Field(default=10, ge=1, le=100)


class PipelineRunState(BaseModel):
    """Stable, durable state of the most recent pipeline run (or idle state)."""

    run_id: str | None
    status: str
    mode: str
    current_job_id: str | None
    current_phase: str
    last_action: str
    last_error: str
    jobs_total: int
    jobs_completed: int
    jobs_failed: int
    jobs_skipped: int
    started_at: str | None
    finished_at: str | None
    cancel_reason: str
    errors: list[dict[str, Any]]
    message: str = ""


def _state_response(worker: Any, message: str) -> dict[str, Any]:
    """Merge worker state with a human message for the response body."""
    state = worker.get_state_dict()
    state["message"] = message
    return state


def _idle_state(message: str) -> dict[str, Any]:
    """An idle-state response body (used when no worker is registered)."""
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
        "message": message,
    }


def _require_worker(request: Request) -> Any:
    worker = getattr(request.app.state, "pipeline_worker", None)
    if worker is None:
        raise HTTPException(
            status_code=503,
            detail="Pipeline worker service not initialized",
        )
    return worker


@router.post("/pipeline/start", response_model=PipelineRunState)
def start_pipeline(request: Request, body: PipelineStartRequest) -> dict[str, Any]:
    """Start a background dry-run pipeline.

    Returns immediately with the durable run state. The run executes in a
    worker subprocess; poll ``GET /api/pipeline/status`` for progress.

    Safety:
    - Only one active pipeline at a time (409 if already running/paused).
    - Never performs final submission.
    - Jobs are processed sequentially.
    """
    worker = _require_worker(request)
    try:
        worker.start(max_jobs=body.max_jobs, fixture_html=body.fixture_html or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _state_response(worker, _SAFETY_MESSAGE)


@router.post("/pipeline/pause", response_model=PipelineRunState)
def pause_pipeline(request: Request) -> dict[str, Any]:
    """Request pause. The worker finishes the current job, then pauses."""
    worker = _require_worker(request)
    try:
        worker.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _state_response(worker, _SAFETY_MESSAGE)


@router.post("/pipeline/resume", response_model=PipelineRunState)
def resume_pipeline(request: Request) -> dict[str, Any]:
    """Resume a paused pipeline."""
    worker = _require_worker(request)
    try:
        worker.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _state_response(worker, _SAFETY_MESSAGE)


@router.post("/pipeline/cancel", response_model=PipelineRunState)
def cancel_pipeline(request: Request) -> dict[str, Any]:
    """Request cancellation. The worker stops before the next job."""
    worker = _require_worker(request)
    worker.cancel()
    return _state_response(worker, _SAFETY_MESSAGE)


@router.get("/pipeline/status", response_model=PipelineRunState)
def get_pipeline_status(request: Request) -> dict[str, Any]:
    """Return the current pipeline state.

    Safe to poll repeatedly. State is durable — it survives API/server
    restarts and the run_id/counts/progress persist in SQLite.
    """
    try:
        worker = _require_worker(request)
    except HTTPException:
        return _idle_state("Pipeline worker not initialized")
    return _state_response(worker, _SAFETY_MESSAGE)
