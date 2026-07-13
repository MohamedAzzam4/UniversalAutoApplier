"""Dashboard status API.

Per ROADMAP WP 6.1: show current pipeline status, active job, last action,
last error, and run mode.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from universal_auto_applier import __version__

router = APIRouter(tags=["dashboard"])


class PipelineStatus(BaseModel):
    """Current pipeline status for the dashboard."""

    run_status: str = "idle"
    current_phase: str = ""
    active_job_id: str | None = None
    active_company: str = ""
    active_title: str = ""
    active_platform: str = ""
    last_action: str = ""
    last_error: str = ""
    submit_mode: str = "review"
    version: str = __version__
    jobs_total: int = 0
    jobs_by_status: dict[str, int] = {}
    pending_interventions: int = 0


@router.get("/status", response_model=PipelineStatus)
def get_status(request: Request) -> PipelineStatus:
    """Return the current pipeline status.

    Phase 6: reads from the database to show job counts and pending
    interventions. The pipeline is currently idle (no worker running yet —
    that's Phase 8). When the worker exists, this endpoint will show
    real-time status.
    """
    from sqlalchemy import func, select

    from universal_auto_applier.interventions.store import count_pending_interventions
    from universal_auto_applier.persistence.job_repository import count_application_jobs
    from universal_auto_applier.persistence.models import ApplicationJobRow

    app = request.app
    session_factory = app.state.session_factory

    with session_factory() as session:
        total = count_application_jobs(session)

        # Count jobs by status.
        stmt = select(ApplicationJobRow.status, func.count()).group_by(ApplicationJobRow.status)
        rows = session.execute(stmt).all()
        by_status = {row[0]: row[1] for row in rows}

        pending = count_pending_interventions(session)

    settings = app.state.settings

    return PipelineStatus(
        run_status="idle",
        current_phase="",
        active_job_id=None,
        submit_mode=settings.submit_mode,
        jobs_total=total,
        jobs_by_status=by_status,
        pending_interventions=pending,
    )
