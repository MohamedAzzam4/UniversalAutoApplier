"""Queue and history API.

Per ROADMAP WP 6.2: show job queue and history with filtering.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
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
    url: str = ""
    cv_url: str | None = None
    cover_letter_url: str | None = None
    submitted: bool = False
    submitted_editable: bool = True
    submitted_source: str | None = None
    step: int = 1


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

        def step_for(status_value: str) -> int:
            if status_value in {"discovered"}:
                return 1
            if status_value in {"evaluated", "rejected"}:
                return 2
            if status_value in {"tailored", "ready_to_apply", "queued"}:
                return 3
            return 4

        def dashboard_submitted(row: ApplicationJobRow) -> bool:
            metadata = cast(dict[str, object], row.metadata_json or {})
            return metadata.get("dashboard_submitted") is True

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
                url=row.url,
                cv_url=(
                    f"/api/queue/{row.application_id}/artifact/cv"
                    if row.cv_pdf and Path(row.cv_pdf).is_file()
                    else None
                ),
                cover_letter_url=(
                    f"/api/queue/{row.application_id}/artifact/cover"
                    if row.cover_letter_pdf and Path(row.cover_letter_pdf).is_file()
                    else None
                ),
                submitted=(row.status in {"submitted", "applied"} or dashboard_submitted(row)),
                submitted_editable=row.status not in {"submitted", "applied"},
                submitted_source=(
                    "workflow"
                    if row.status in {"submitted", "applied"}
                    else "dashboard"
                    if dashboard_submitted(row)
                    else None
                ),
                step=step_for(row.status),
            )
            for row in rows
        ]

    return QueueResponse(total=len(jobs), jobs=jobs)


class SubmittedRequest(BaseModel):
    submitted: bool


@router.patch("/queue/{application_id}/submitted")
def set_submitted(
    request: Request,
    application_id: str,
    payload: SubmittedRequest,
) -> dict[str, object]:
    """Set the operator-maintained submitted marker for one history row."""
    from universal_auto_applier.persistence.db import session_scope
    from universal_auto_applier.persistence.job_repository import set_manual_submitted

    with session_scope(request.app.state.session_factory) as session:
        try:
            row = set_manual_submitted(session, application_id, payload.submitted)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "application_id": row.application_id,
            "submitted": payload.submitted,
            "submitted_editable": True,
            "submitted_source": "dashboard" if payload.submitted else None,
        }


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


@router.get("/queue/{application_id}/artifact/{kind}", response_class=FileResponse)
def get_job_artifact(request: Request, application_id: str, kind: str) -> FileResponse:
    """Open a generated CV or cover letter inline in the browser."""
    if kind not in {"cv", "cover"}:
        raise HTTPException(status_code=404, detail="Unknown artifact type")

    from universal_auto_applier.persistence.job_repository import get_application_job

    with request.app.state.session_factory() as session:
        job = get_application_job(session, application_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    raw_path = job.cv_pdf if kind == "cv" else job.cover_letter_pdf
    path = Path(raw_path).resolve() if raw_path else None
    if path is None or path.suffix.lower() != ".pdf" or not path.is_file():
        raise HTTPException(status_code=404, detail="Document file is missing")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )
