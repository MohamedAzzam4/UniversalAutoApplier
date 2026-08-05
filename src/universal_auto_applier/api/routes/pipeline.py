"""Pipeline start/control API.

WQ-4: Background sequential browser pipeline with dashboard controls.

Endpoints:
- POST /api/pipeline/start — start background pipeline (returns immediately)
- POST /api/pipeline/pause — request pause
- POST /api/pipeline/resume — resume paused pipeline
- POST /api/pipeline/cancel — request cancellation
- GET  /api/pipeline/status — current pipeline state (durable, pollable)

The pipeline runs in a background thread via PipelineWorkerService.
It never performs final submission — only dry-run/review behavior.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["pipeline"])


class PipelineStartRequest(BaseModel):
    """Request to start the pipeline."""

    fixture_html: str | None = None  # Legacy: fixture mode (not used for live browser)
    max_jobs: int = 10


class PipelineStartResponse(BaseModel):
    """Response from starting the pipeline."""

    run_id: str
    status: str
    jobs_total: int
    jobs_completed: int
    jobs_failed: int
    jobs_skipped: int
    message: str = ""


class PipelineControlResponse(BaseModel):
    """Response from pause/resume/cancel."""

    status: str
    run_id: str
    message: str = ""


@router.post("/pipeline/start")
def start_pipeline(request: Request, body: PipelineStartRequest) -> dict[str, Any]:
    """Start a background dry-run pipeline.

    Returns immediately with the run_id. The pipeline runs in a background
    thread. Poll GET /pipeline/status for progress.

    Safety:
    - Only one active pipeline at a time (409 if already running/paused).
    - Never performs final submission.
    - Jobs are processed sequentially through LiveBrowserRunner.
    """
    app = request.app
    worker = getattr(app.state, "pipeline_worker", None)
    if worker is None:
        raise HTTPException(
            status_code=503,
            detail="Pipeline worker service not initialized",
        )

    try:
        state = worker.start(max_jobs=body.max_jobs)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return state


@router.post("/pipeline/pause")
def pause_pipeline(request: Request) -> dict[str, Any]:
    """Request pause. The worker finishes the current job, then pauses."""
    app = request.app
    worker = getattr(app.state, "pipeline_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Pipeline worker not initialized")

    try:
        return worker.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pipeline/resume")
def resume_pipeline(request: Request) -> dict[str, Any]:
    """Resume a paused pipeline."""
    app = request.app
    worker = getattr(app.state, "pipeline_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Pipeline worker not initialized")

    try:
        return worker.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/pipeline/cancel")
def cancel_pipeline(request: Request) -> dict[str, Any]:
    """Request cancellation. The worker stops before the next job."""
    app = request.app
    worker = getattr(app.state, "pipeline_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Pipeline worker not initialized")

    return worker.cancel()


@router.get("/pipeline/status")
def get_pipeline_status(request: Request) -> dict[str, Any]:
    """Return the current pipeline state.

    Safe to poll repeatedly. State is durable and survives API reloads
    (in-memory state persists for the lifetime of the server process).
    """
    app = request.app
    worker = getattr(app.state, "pipeline_worker", None)
    if worker is None:
        return {
            "status": "idle",
            "run_id": None,
            "current_job_id": None,
            "current_phase": "",
            "last_action": "",
            "last_error": "",
            "jobs_total": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_skipped": 0,
            "message": "Pipeline worker not initialized",
        }

    state = worker.get_state_dict()
    state["message"] = "Background dry-run pipeline. No final submissions occur from this pipeline."
    return state
