"""Repository and store methods for :class:`ApplicationJob`.

Per ``IMPLEMENTATION_RULES.md`` -> Store Access:

- Do not mutate ``history._data`` outside store classes.
- Do not open and rewrite history JSON from random modules.
- Add store methods for every new state transition.
- Store methods should be idempotent where practical.

Required store methods (Phase 1 subset):

- ``upsert_application_job`` — idempotent insert-or-update.

Later phases add: ``record_attempt_started``, ``record_phase_result``,
``record_intervention``, ``resolve_intervention``, ``mark_review_ready``,
``mark_applied``, ``mark_failed``, ``mark_skipped``, ``mark_blocked``.

All timestamps are timezone-aware UTC. ``first_seen_at`` is preserved on
update; ``last_updated_at`` is always refreshed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ApplicationStatus,
)
from universal_auto_applier.persistence.models import ApplicationJobRow


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _row_to_job(row: ApplicationJobRow) -> ApplicationJob:
    """Convert an :class:`ApplicationJobRow` to an :class:`ApplicationJob`."""
    from universal_auto_applier.core.models import ApplicationJobDocuments

    documents: ApplicationJobDocuments | None = None
    if row.documents_json:
        documents = ApplicationJobDocuments(**row.documents_json)

    return ApplicationJob(
        application_id=row.application_id,
        platform=row.platform,  # type: ignore[arg-type]
        source=row.source,
        company=row.company,
        title=row.title,
        url=row.url,
        location=row.location,
        job_description=row.job_description,
        score=row.score,
        verdict=row.verdict,
        cv_pdf=row.cv_pdf,
        cover_letter_pdf=row.cover_letter_pdf,
        status=row.status,  # type: ignore[arg-type]
        job_id=row.job_id,
        external_job_id=row.external_job_id,
        date_posted=row.date_posted,
        evaluated_at=row.evaluated_at,
        tailored_at=row.tailored_at,
        evaluation_reason=row.evaluation_reason,
        german_filter_result=row.german_filter_result,
        documents=documents,
        metadata=row.metadata_json or {},
    )


def _job_to_row_data(job: ApplicationJob) -> dict[str, object]:
    """Convert an :class:`ApplicationJob` to a dict suitable for row construction."""
    return {
        "application_id": job.application_id,
        "platform": str(job.platform),
        "source": job.source,
        "company": job.company,
        "title": job.title,
        "url": job.url,
        "location": job.location,
        "job_description": job.job_description,
        "score": job.score,
        "verdict": job.verdict,
        "cv_pdf": job.cv_pdf,
        "cover_letter_pdf": job.cover_letter_pdf,
        "status": str(job.status),
        "job_id": job.job_id,
        "external_job_id": job.external_job_id,
        "date_posted": job.date_posted,
        "evaluated_at": job.evaluated_at,
        "tailored_at": job.tailored_at,
        "evaluation_reason": job.evaluation_reason,
        "german_filter_result": job.german_filter_result,
        "documents_json": job.documents.model_dump() if job.documents else None,
        "metadata_json": job.metadata,
    }


def upsert_application_job(session: Session, job: ApplicationJob) -> ApplicationJobRow:
    """Insert or update ``job`` idempotently.

    Rules:
    - If the job does not exist, insert it with ``first_seen_at = now`` and
      ``last_updated_at = now``.
    - If the job exists, update all descriptive fields (company, title, url,
      score, verdict, documents, etc.) and refresh ``last_updated_at``.
      ``first_seen_at`` is preserved.
    - Re-import may update descriptive job metadata and artifact paths, but
      it must **not** erase attempt history or downgrade a final state.
      If the existing row is in a terminal status (applied, rejected,
      skipped, closed), the status field is not overwritten.

    Args:
        session: An open SQLAlchemy session. The caller is responsible for
            commit/rollback (see ``session_scope``).
        job: A validated :class:`ApplicationJob`.

    Returns:
        The persisted :class:`ApplicationJobRow`.
    """
    existing = session.get(ApplicationJobRow, job.application_id)

    if existing is None:
        # Insert.
        row = ApplicationJobRow(
            **_job_to_row_data(job),
            first_seen_at=_utcnow(),
            last_updated_at=_utcnow(),
        )
        session.add(row)
        session.flush()
        return row

    # Update descriptive fields, but preserve first_seen_at.
    existing.platform = str(job.platform)
    existing.source = job.source
    existing.company = job.company
    existing.title = job.title
    existing.url = job.url
    existing.location = job.location
    existing.job_description = job.job_description
    existing.score = job.score
    existing.verdict = job.verdict
    existing.cv_pdf = job.cv_pdf
    existing.cover_letter_pdf = job.cover_letter_pdf
    existing.job_id = job.job_id
    existing.external_job_id = job.external_job_id
    existing.date_posted = job.date_posted
    existing.evaluated_at = job.evaluated_at
    existing.tailored_at = job.tailored_at
    existing.evaluation_reason = job.evaluation_reason
    existing.german_filter_result = job.german_filter_result
    existing.documents_json = job.documents.model_dump() if job.documents else None
    existing.metadata_json = job.metadata

    # Do not downgrade terminal statuses on re-import.
    current_status = ApplicationStatus(existing.status)
    new_status = job.status
    if current_status not in TERMINAL_STATUSES and new_status != current_status:
        # Only update status if the transition is allowed.
        if new_status in ALLOWED_TRANSITIONS.get(current_status, frozenset()):
            existing.status = str(new_status)
        # If the transition is not allowed, keep the current status.
        # This prevents re-import from violating the state machine.

    existing.last_updated_at = _utcnow()
    session.flush()
    return existing


def get_application_job(session: Session, application_id: str) -> ApplicationJob | None:
    """Return the :class:`ApplicationJob` with ``application_id``, or None."""
    row = session.get(ApplicationJobRow, application_id)
    if row is None:
        return None
    return _row_to_job(row)


def list_application_jobs(session: Session) -> list[ApplicationJob]:
    """Return all application jobs, ordered by ``first_seen_at``."""
    stmt = select(ApplicationJobRow).order_by(ApplicationJobRow.first_seen_at)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_job(row) for row in rows]


def count_application_jobs(session: Session) -> int:
    """Return the total number of application jobs."""
    stmt = select(ApplicationJobRow)
    return len(list(session.execute(stmt).scalars().all()))


def update_application_status(
    session: Session,
    application_id: str,
    new_status: ApplicationStatus,
) -> ApplicationJobRow | None:
    """Update the status of an application job.

    Validates the transition against :data:`ALLOWED_TRANSITIONS`. If the
    transition is not allowed, raises :class:`ValueError`.

    Returns the updated row, or None if the job was not found.
    """
    row = session.get(ApplicationJobRow, application_id)
    if row is None:
        return None

    current = ApplicationStatus(str(row.status))
    target = ApplicationStatus(str(new_status))
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValueError(
            f"status transition {current} -> {target} is not allowed "
            f"(allowed: {sorted(s.value for s in allowed)})"
        )

    row.status = str(target)
    session.flush()
    return row


__all__ = [
    "upsert_application_job",
    "get_application_job",
    "list_application_jobs",
    "count_application_jobs",
    "update_application_status",
]
