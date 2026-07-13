"""Retry API.

Per ROADMAP WP 6.4: "Add retry controls for safe phases."

Phase 6: The retry API is a thin wrapper that marks a job for re-queueing.
It does NOT execute any browser or submission action. The actual retry
execution belongs to Phase 8 (pipeline orchestration).

Safety:
- Retry only re-queues a job; it does not submit.
- Retry is rejected for jobs in terminal statuses (applied, rejected,
  skipped, closed) to prevent duplicate submissions.
- Retry does not duplicate submitted applications.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["retry"])


class RetryResponse(BaseModel):
    """Response for the retry endpoint."""

    application_id: str
    status: str
    message: str


@router.post("/queue/{application_id}/retry", response_model=RetryResponse)
def retry_job(request: Request, application_id: str) -> RetryResponse:
    """Mark a job for retry by re-queueing it.

    This endpoint:
    1. Checks the job exists.
    2. Rejects retry if the job is in a terminal status (applied, rejected,
       skipped, closed).
    3. If the job is in a retryable status (failed, blocked, needs_review),
       marks it as queued for the next pipeline run.
    4. Does NOT execute any browser or submission action.

    The actual retry execution (re-navigation, re-filling) belongs to
    Phase 8 (pipeline orchestration).
    """
    from universal_auto_applier.core.statuses import ALLOWED_TRANSITIONS, ApplicationStatus
    from universal_auto_applier.persistence.job_repository import get_application_job
    from universal_auto_applier.persistence.models import ApplicationJobRow

    app = request.app
    session_factory = app.state.session_factory

    with session_factory() as session:
        job = get_application_job(session, application_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        current_status = job.status
        # Terminal statuses cannot be retried.
        if current_status in {
            ApplicationStatus.APPLIED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.SKIPPED,
            ApplicationStatus.CLOSED,
        }:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot retry job in terminal status: {current_status}",
            )

        # Check if transition to queued is allowed.
        if ApplicationStatus.QUEUED not in ALLOWED_TRANSITIONS.get(current_status, frozenset()):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot retry job from status {current_status}: transition to queued not allowed",
            )

        # Re-queue the job.
        row = session.get(ApplicationJobRow, application_id)
        if row is not None:
            row.status = str(ApplicationStatus.QUEUED)
            session.commit()

    return RetryResponse(
        application_id=application_id,
        status="queued",
        message="Job re-queued for retry. The pipeline will process it in the next run.",
    )
