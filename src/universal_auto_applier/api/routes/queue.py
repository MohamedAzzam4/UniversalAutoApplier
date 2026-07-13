"""Queue and history API.

Per ROADMAP WP 6.2: show job queue and history with filtering.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

router = APIRouter(tags=["queue"])


class JobSummary(BaseModel):
    """A job summary for the queue/history view."""

    application_id: str
    platform: str
    company: str
    title: str
    status: str
    score: float | None = None
    source: str = ""
    location: str | None = None
    first_seen_at: str = ""
    last_updated_at: str = ""
    last_error: str = ""


class QueueResponse(BaseModel):
    """Response for the queue endpoint."""

    total: int
    jobs: list[JobSummary]


@router.get("/queue", response_model=QueueResponse)
def get_queue(
    request: Request,
    status: str | None = Query(default=None, description="Filter by status"),
    platform: str | None = Query(default=None, description="Filter by platform"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> QueueResponse:
    """Return the job queue/history.

    Supports filtering by status and platform, with pagination.
    """
    from sqlalchemy import select

    from universal_auto_applier.persistence.models import ApplicationJobRow

    app = request.app
    session_factory = app.state.session_factory

    with session_factory() as session:
        stmt = select(ApplicationJobRow)

        if status:
            stmt = stmt.where(ApplicationJobRow.status == status)
        if platform:
            stmt = stmt.where(ApplicationJobRow.platform == platform)

        stmt = stmt.order_by(ApplicationJobRow.last_updated_at.desc())
        stmt = stmt.offset(offset).limit(limit)

        rows = session.execute(stmt).scalars().all()

        jobs = [
            JobSummary(
                application_id=row.application_id,
                platform=row.platform,
                company=row.company,
                title=row.title,
                status=row.status,
                score=row.score,
                source=row.source,
                location=row.location,
                first_seen_at=row.first_seen_at.isoformat() if row.first_seen_at else "",
                last_updated_at=row.last_updated_at.isoformat() if row.last_updated_at else "",
            )
            for row in rows
        ]

    return QueueResponse(total=len(jobs), jobs=jobs)


class JobDetailResponse(BaseModel):
    """Detailed job information."""

    application_id: str
    platform: str
    source: str
    company: str
    title: str
    url: str
    location: str | None = None
    job_description: str | None = None
    score: float | None = None
    verdict: str = ""
    cv_pdf: str | None = None
    cover_letter_pdf: str | None = None
    status: str = ""
    external_job_id: str | None = None
    first_seen_at: str = ""
    last_updated_at: str = ""


@router.get("/queue/{application_id}", response_model=JobDetailResponse)
def get_job_detail(request: Request, application_id: str) -> JobDetailResponse:
    """Return detailed information for a single job."""
    from universal_auto_applier.persistence.job_repository import get_application_job

    app = request.app
    session_factory = app.state.session_factory

    with session_factory() as session:
        job = get_application_job(session, application_id)

    if job is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Job not found")

    return JobDetailResponse(
        application_id=job.application_id,
        platform=str(job.platform),
        source=job.source,
        company=job.company,
        title=job.title,
        url=job.url,
        location=job.location,
        job_description=job.job_description,
        score=job.score,
        verdict=job.verdict,
        cv_pdf=job.cv_pdf,
        cover_letter_pdf=job.cover_letter_pdf,
        status=str(job.status),
        external_job_id=job.external_job_id,
    )
