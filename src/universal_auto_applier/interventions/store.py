"""Intervention store — create, resolve, and list interventions.

Per ``ROADMAP.md`` WP 5.1, the intervention queue stores user-facing tasks
that require human action. Interventions are created when:
- A required field has no mapping (field_answer)
- A login page is detected (login_required)
- A captcha is detected (captcha)
- A review/submit page is reached (review_before_submit)
- A document is missing (missing_document)
- A validation error occurs (validation_error)
- A manual upload is needed (manual_upload_required)
- An unknown page is encountered (unknown_page)

The store uses SQLAlchemy for persistence. All mutations go through store
methods only.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.core.models import Intervention
from universal_auto_applier.core.statuses import InterventionKind, InterventionStatus
from universal_auto_applier.persistence.models import InterventionRow

logger = logging.getLogger("universal_auto_applier.interventions.store")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _make_intervention_id(
    application_id: str,
    kind: str,
    field_selector: str | None = None,
    question: str = "",
) -> str:
    """Generate a deterministic intervention ID.

    The ID is stable for the same (application_id, kind, field_selector,
    question) combination, so re-creating an intervention for the same
    field is idempotent.
    """
    source = f"{application_id}:{kind}:{field_selector or ''}:{question}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


def _row_to_intervention(row: InterventionRow) -> Intervention:
    """Convert an :class:`InterventionRow` to an :class:`Intervention`."""
    return Intervention(
        intervention_id=row.intervention_id,
        application_id=row.application_id,
        status=InterventionStatus(row.status),
        kind=InterventionKind(row.kind),
        question=row.question,
        options=row.options or [],
        suggested_answer=row.suggested_answer,
        confidence=row.confidence,
        field_selector=row.field_selector,
        page_url=row.page_url,
        screenshot=row.screenshot,
        llm_metadata=row.llm_metadata_json,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def create_intervention(
    session: Session,
    *,
    application_id: str,
    kind: InterventionKind,
    question: str,
    options: list[str] | None = None,
    suggested_answer: str | None = None,
    confidence: float | None = None,
    field_selector: str | None = None,
    page_url: str | None = None,
    screenshot: str | None = None,
    llm_metadata: dict[str, Any] | None = None,
) -> InterventionRow:
    """Create a new intervention or return the existing one if it already exists.

    The intervention ID is deterministic based on (application_id, kind,
    field_selector, question), so creating the same intervention twice is
    idempotent — it returns the existing row without creating a duplicate.

    Args:
        session: An open SQLAlchemy session.
        application_id: The job/application ID.
        kind: The intervention kind (field_answer, login_required, etc.).
        question: Human-readable question or description.
        options: Allowed answer options (for field_answer).
        suggested_answer: Suggested answer if available.
        confidence: Confidence of the suggested answer (0.0-1.0).
        field_selector: CSS selector of the related field, if applicable.
        page_url: URL of the page where the intervention was triggered.
        screenshot: Path to a screenshot, if available.
        llm_metadata: Optional structured LLM metadata dict containing
            available_options, evidence_summary, category, risk_level,
            requires_confirmation, unresolved_reason, field_token,
            answer_source.

    Returns:
        The :class:`InterventionRow` (newly created or existing).
    """
    intervention_id = _make_intervention_id(application_id, str(kind), field_selector, question)

    existing = session.get(InterventionRow, intervention_id)
    if existing is not None:
        logger.info(
            "[%s] intervention already exists: id=%s kind=%s",
            application_id[:12],
            intervention_id[:12],
            kind,
        )
        return existing

    row = InterventionRow(
        intervention_id=intervention_id,
        application_id=application_id,
        status=str(InterventionStatus.PENDING),
        kind=str(kind),
        question=question,
        options=options or [],
        suggested_answer=suggested_answer,
        confidence=confidence,
        field_selector=field_selector,
        page_url=page_url,
        screenshot=screenshot,
        llm_metadata_json=llm_metadata,
        created_at=_utcnow(),
        resolved_at=None,
    )
    session.add(row)
    session.flush()

    logger.info(
        "[%s] intervention created: id=%s kind=%s question=%s",
        application_id[:12],
        intervention_id[:12],
        kind,
        question[:50],
    )
    return row


def resolve_intervention(
    session: Session,
    intervention_id: str,
    *,
    resolution: InterventionStatus,
    answer: str | None = None,
) -> InterventionRow | None:
    """Resolve an intervention with a user decision.

    Args:
        session: An open SQLAlchemy session.
        intervention_id: The intervention ID to resolve.
        resolution: The resolution status (approved, edited, skipped, blocked, resolved).
        answer: The user's answer, if applicable (used for answer memory).

    Returns:
        The updated :class:`InterventionRow`, or None if not found.

    Raises:
        ValueError: If the resolution status is not a valid terminal status
            (must be one of: approved, edited, skipped, blocked, resolved).
    """
    valid_resolutions = {
        InterventionStatus.APPROVED,
        InterventionStatus.EDITED,
        InterventionStatus.SKIPPED,
        InterventionStatus.BLOCKED,
        InterventionStatus.RESOLVED,
    }
    if resolution not in valid_resolutions:
        raise ValueError(
            f"Invalid resolution status: {resolution}. Must be one of {valid_resolutions}"
        )

    row = session.get(InterventionRow, intervention_id)
    if row is None:
        return None

    row.status = str(resolution)
    row.resolved_at = _utcnow()

    # If an answer was provided and the resolution is approved or edited,
    # update the suggested answer.
    if answer is not None and resolution in (
        InterventionStatus.APPROVED,
        InterventionStatus.EDITED,
    ):
        row.suggested_answer = answer

    session.flush()
    logger.info(
        "intervention resolved: id=%s resolution=%s",
        intervention_id[:12],
        resolution,
    )
    return row


def list_pending_interventions(
    session: Session,
    application_id: str | None = None,
) -> list[Intervention]:
    """List pending interventions, optionally filtered by application_id."""
    stmt = select(InterventionRow).where(InterventionRow.status == str(InterventionStatus.PENDING))
    if application_id is not None:
        stmt = stmt.where(InterventionRow.application_id == application_id)
    stmt = stmt.order_by(InterventionRow.created_at)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_intervention(row) for row in rows]


def list_all_interventions(
    session: Session,
    application_id: str | None = None,
) -> list[Intervention]:
    """List all interventions, optionally filtered by application_id."""
    stmt = select(InterventionRow)
    if application_id is not None:
        stmt = stmt.where(InterventionRow.application_id == application_id)
    stmt = stmt.order_by(InterventionRow.created_at)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_intervention(row) for row in rows]


def get_intervention(
    session: Session,
    intervention_id: str,
) -> Intervention | None:
    """Return a single intervention by ID, or None."""
    row = session.get(InterventionRow, intervention_id)
    if row is None:
        return None
    return _row_to_intervention(row)


def count_pending_interventions(
    session: Session,
    application_id: str | None = None,
) -> int:
    """Count pending interventions, optionally filtered by application_id."""
    stmt = select(InterventionRow).where(InterventionRow.status == str(InterventionStatus.PENDING))
    if application_id is not None:
        stmt = stmt.where(InterventionRow.application_id == application_id)
    return len(list(session.execute(stmt).scalars().all()))


def find_pending_intervention_for_field(
    session: Session,
    application_id: str,
    kind: InterventionKind,
    field_selector: str,
    question: str,
) -> InterventionRow | None:
    """Find a PENDING intervention for a specific field, if one exists.

    Used by the CLI persistence layer to supersede stale pending
    interventions when a field that was previously unresolved is now
    successfully filled. The lookup uses the deterministic intervention
    ID (derived from application_id + kind + field_selector + question),
    so it finds the exact intervention created for this field on a
    previous run — regardless of how many other interventions exist.

    Args:
        session: An open SQLAlchemy session.
        application_id: The job/application ID.
        kind: The intervention kind (typically FIELD_ANSWER).
        field_selector: The stable field token (e.g. ``lf-a1b2c3d4``).
        question: The question text used when the intervention was created.

    Returns:
        The :class:`InterventionRow` if a PENDING intervention exists for
        this field, or None if no such intervention exists (or if it
        exists but is already resolved).
    """
    intervention_id = _make_intervention_id(application_id, str(kind), field_selector, question)
    row = session.get(InterventionRow, intervention_id)
    if row is None:
        return None
    if row.status != str(InterventionStatus.PENDING):
        return None
    return row


__all__ = [
    "create_intervention",
    "resolve_intervention",
    "list_pending_interventions",
    "list_all_interventions",
    "get_intervention",
    "count_pending_interventions",
    "find_pending_intervention_for_field",
]
