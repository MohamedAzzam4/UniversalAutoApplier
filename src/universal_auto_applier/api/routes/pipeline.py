"""Pipeline start/control API.

Per ROADmap Phase 8: "Run everything from the dashboard or CLI."

This endpoint starts a safe dry-run pipeline run. It does NOT submit
real applications. The pipeline runs synchronously (blocking) for now;
a background worker is a future enhancement.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["pipeline"])


class PipelineStartRequest(BaseModel):
    """Request to start the pipeline."""

    fixture_html: str | None = None
    max_jobs: int = 10


class PipelineStartResponse(BaseModel):
    """Response from starting the pipeline."""

    run_id: str
    status: str
    jobs_processed: int
    jobs_succeeded: int
    jobs_failed: int
    jobs_skipped: int
    last_error: str = ""
    message: str = ""


@router.post("/pipeline/start", response_model=PipelineStartResponse)
def start_pipeline(request: Request, body: PipelineStartRequest) -> PipelineStartResponse:
    """Start a safe dry-run pipeline run.

    This endpoint:
    1. Rejects if a pipeline is already running.
    2. Creates a PipelineOrchestrator.
    3. Runs it synchronously with the provided fixture HTML (if any).
    4. Sets app.state.pipeline_state to the result.
    5. Returns the pipeline state.

    Safety:
    - Default mode is review (dry-run). No real submission occurs.
    - The orchestrator checks review approval before any submit_or_pause call.
    - This endpoint does NOT submit applications.
    """
    from universal_auto_applier.config import Settings
    from universal_auto_applier.core.models import CandidateProfile
    from universal_auto_applier.services.pipeline_orchestrator import PipelineOrchestrator

    app = request.app

    # Check if a pipeline is already running.
    existing_state = getattr(app.state, "pipeline_state", None)
    if existing_state is not None and existing_state.status == "running":
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    settings: Settings = app.state.settings
    session_factory = app.state.session_factory

    # Create and run the orchestrator.
    orchestrator = PipelineOrchestrator(
        settings=settings,
        session_factory=session_factory,
        candidate=CandidateProfile(),
    )

    # Set the pipeline state on app.state BEFORE running so the status
    # endpoint can see "running" during the synchronous run.
    app.state.pipeline_state = orchestrator.state

    try:
        state = orchestrator.run(
            fixture_html=body.fixture_html,
            max_jobs=body.max_jobs,
        )
    except Exception as exc:
        state = orchestrator.state
        state.status = "error"
        state.last_error = str(exc)

    # Update app.state with the final state.
    app.state.pipeline_state = state

    return PipelineStartResponse(
        run_id=state.run_id,
        status=state.status,
        jobs_processed=state.jobs_processed,
        jobs_succeeded=state.jobs_succeeded,
        jobs_failed=state.jobs_failed,
        jobs_skipped=state.jobs_skipped,
        last_error=state.last_error,
        message="Pipeline run completed. No real submissions occurred."
        if state.status == "completed"
        else f"Pipeline {state.status}.",
    )


@router.get("/pipeline/status")
def get_pipeline_status(request: Request) -> dict[str, Any]:
    """Return the current pipeline state."""
    app = request.app
    state = getattr(app.state, "pipeline_state", None)

    if state is None:
        return {
            "status": "idle",
            "run_id": None,
            "current_job_id": None,
            "current_phase": "",
            "last_action": "",
            "last_error": "",
            "jobs_processed": 0,
            "jobs_succeeded": 0,
            "jobs_failed": 0,
            "jobs_skipped": 0,
        }

    return {
        "status": state.status,
        "run_id": state.run_id,
        "current_job_id": state.current_job_id,
        "current_phase": state.current_phase,
        "last_action": state.last_action,
        "last_error": state.last_error,
        "jobs_processed": state.jobs_processed,
        "jobs_succeeded": state.jobs_succeeded,
        "jobs_failed": state.jobs_failed,
        "jobs_skipped": state.jobs_skipped,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "finished_at": state.finished_at.isoformat() if state.finished_at else None,
    }
