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

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    AdapterResultStatus,
    ApplicationStatus,
    AttemptMode,
    Phase,
)
from universal_auto_applier.persistence.models import (
    ApplicationAttemptRow,
    ApplicationJobRow,
    PhaseResultRow,
)

_UAA_SUBMISSION_MARKER_KEYS = ("dashboard_submitted", "dashboard_submitted_at")
_UAA_FIELD_CORRECTIONS_KEY = "_uaa_field_corrections"
_QUESTION_ANSWER_METADATA_KEYS = ("application_answers", "form_answers", "question_answers")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _metadata_for_insert(metadata: dict[str, object]) -> dict[str, object]:
    """Copy producer metadata without accepting UAA-owned submission markers."""
    result = dict(metadata)
    for key in (*_UAA_SUBMISSION_MARKER_KEYS, _UAA_FIELD_CORRECTIONS_KEY):
        result.pop(key, None)
    return result


def _metadata_for_reimport(
    existing_metadata: dict[str, object] | None,
    producer_metadata: dict[str, object],
) -> dict[str, object]:
    """Refresh producer data while retaining narrowly owned UAA state.

    Dashboard submission markers are authoritative once set in the local
    database. Per-job answer maps are retained only when the producer omits
    the whole key; an explicitly supplied producer map replaces the old map.
    This is intentionally a shallow, key-specific policy rather than a general
    metadata merge.
    """
    existing = existing_metadata or {}
    result = _metadata_for_insert(producer_metadata)

    for key in _UAA_SUBMISSION_MARKER_KEYS:
        if key in existing:
            result[key] = existing[key]

    # Corrections are UAA-owned, job-local operator state. Queue re-imports
    # must neither erase it nor accept a producer-supplied replacement.
    if _UAA_FIELD_CORRECTIONS_KEY in existing:
        result[_UAA_FIELD_CORRECTIONS_KEY] = existing[_UAA_FIELD_CORRECTIONS_KEY]

    for key in _QUESTION_ANSWER_METADATA_KEYS:
        if key not in result and key in existing:
            result[key] = existing[key]

    return result


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
      Producer metadata is refreshed while UAA-owned dashboard submission
      markers are retained. Known per-job answer maps are retained only when
      the producer omits the whole key; an explicit map replaces the old one.
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
        row_data = _job_to_row_data(job)
        row_data["metadata_json"] = _metadata_for_insert(job.metadata)
        row = ApplicationJobRow(
            **row_data,
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
    existing.metadata_json = _metadata_for_reimport(
        existing.metadata_json,
        job.metadata,
    )

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


def set_uaa_field_corrections(
    session: Session,
    application_id: str,
    corrections: dict[str, object],
) -> ApplicationJobRow | None:
    """Persist UAA-owned field corrections without treating them as producer metadata."""
    row = session.get(ApplicationJobRow, application_id)
    if row is None:
        return None
    metadata = dict(row.metadata_json or {})
    metadata[_UAA_FIELD_CORRECTIONS_KEY] = corrections
    row.metadata_json = metadata
    row.last_updated_at = _utcnow()
    session.flush()
    return row


def set_manual_submitted(
    session: Session,
    application_id: str,
    submitted: bool,
) -> ApplicationJobRow | None:
    """Persist an operator-maintained submitted marker in job metadata.

    This deliberately does not forge the canonical ``submitted``/``applied``
    lifecycle states, which require controlled ATS evidence.  Canonically
    submitted jobs are locked; dashboard-owned markers can be toggled.
    """
    row = session.get(ApplicationJobRow, application_id)
    if row is None:
        return None

    canonical_status = ApplicationStatus(str(row.status))
    if not submitted and canonical_status in {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.APPLIED,
    }:
        raise ValueError("A workflow-confirmed submission cannot be cleared")

    metadata = dict(row.metadata_json or {})
    metadata["dashboard_submitted"] = submitted
    metadata["dashboard_submitted_at"] = _utcnow().isoformat()
    row.metadata_json = metadata
    row.last_updated_at = _utcnow()
    session.flush()
    return row


def is_manual_submitted(job: ApplicationJob) -> bool:
    """Return whether an operator marked this job submitted in the dashboard."""
    return bool(job.metadata.get("dashboard_submitted"))


def record_attempt_started(
    session: Session,
    *,
    application_id: str,
    run_id: str,
    adapter: str,
    mode: AttemptMode,
) -> ApplicationAttemptRow:
    """Create one durable attempt row for a pipeline job execution."""
    row = ApplicationAttemptRow(
        attempt_id=uuid.uuid4().hex,
        application_id=application_id,
        run_id=run_id,
        adapter=adapter,
        mode=str(mode),
        status=ApplicationStatus.IN_PROGRESS.value,
        started_at=_utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def record_phase_result(
    session: Session,
    *,
    attempt_id: str,
    phase: Phase,
    status: AdapterResultStatus,
    message: str,
    metadata: dict[str, object] | None = None,
) -> PhaseResultRow:
    """Append an immutable result for one attempt phase."""
    attempt = session.get(ApplicationAttemptRow, attempt_id)
    if attempt is None:
        raise LookupError(f"Application attempt {attempt_id} does not exist")
    latest_sequence = session.scalar(
        select(func.max(PhaseResultRow.sequence)).where(PhaseResultRow.attempt_id == attempt_id)
    )
    row = PhaseResultRow(
        attempt_id=attempt_id,
        sequence=int(latest_sequence or 0) + 1,
        phase=str(phase),
        status=str(status),
        message=message,
        metadata_json=dict(metadata or {}),
        recorded_at=_utcnow(),
    )
    attempt.last_phase = str(phase)
    session.add(row)
    session.flush()
    return row


def finish_attempt(
    session: Session,
    *,
    attempt_id: str,
    status: ApplicationStatus,
) -> ApplicationAttemptRow | None:
    """Mark one attempt complete with its final application status."""
    row = session.get(ApplicationAttemptRow, attempt_id)
    if row is None:
        return None
    row.status = str(status)
    row.finished_at = _utcnow()
    session.flush()
    return row


__all__ = [
    "upsert_application_job",
    "get_application_job",
    "list_application_jobs",
    "count_application_jobs",
    "update_application_status",
    "set_uaa_field_corrections",
    "set_manual_submitted",
    "is_manual_submitted",
    "record_attempt_started",
    "record_phase_result",
    "finish_attempt",
]
