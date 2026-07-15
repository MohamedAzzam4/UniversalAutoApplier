"""Submission store — persisted approvals, claims, and results.

All mutations go through this store. The store enforces:

- One-time approval consumption (an approval can only be claimed once).
- Transactional claim acquisition (compare-and-set via SELECT ... FOR UPDATE
  or equivalent, preventing concurrent duplicate clicks).
- Idempotent result recording (one result per approval).
- Revocation support (user can revoke an approval before the click).

The store is the single source of truth for submission state. In-memory
caches (e.g., dashboard review states) are NOT authoritative.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.persistence.models import (
    SubmissionApprovalRow,
    SubmissionClaimRow,
    SubmissionResultRow,
)
from universal_auto_applier.submission.models import (
    SubmissionApproval,
    SubmissionClaim,
    SubmissionResult,
    SubmissionResultState,
    SubmissionSnapshot,
)

logger = logging.getLogger("universal_auto_applier.submission.store")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _make_id(*parts: str) -> str:
    """Deterministic ID from stable parts, or a UUID if no parts given."""
    if not parts or not any(parts):
        return uuid.uuid4().hex
    source = ":".join(parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Snapshot building
# ---------------------------------------------------------------------------


def build_snapshot(
    *,
    application_id: str,
    application_url: str,
    fields: list[Any],
    uploads: list[Any],
    pending_intervention_count: int,
    submit_control_text: str = "",
    submit_control_selector: str = "",
    submit_control_frame_url: str = "",
) -> SubmissionSnapshot:
    """Build a snapshot from live run report data.

    This is a thin wrapper around
    :func:`universal_auto_applier.submission.models.build_snapshot_from_report`
    that accepts the raw report fields.
    """
    from universal_auto_applier.submission.models import build_snapshot_from_report

    return build_snapshot_from_report(
        application_id=application_id,
        application_url=application_url,
        fields=fields,
        uploads=uploads,
        pending_intervention_count=pending_intervention_count,
        submit_control_text=submit_control_text,
        submit_control_selector=submit_control_selector,
        submit_control_frame_url=submit_control_frame_url,
    )


# ---------------------------------------------------------------------------
# Approval management
# ---------------------------------------------------------------------------


def create_approval(
    session: Session,
    *,
    application_id: str,
    snapshot: SubmissionSnapshot,
) -> SubmissionApprovalRow:
    """Create a new approval for a snapshot.

    If an active (non-consumed, non-revoked) approval already exists for
    the same (application_id, snapshot_hash), it is returned as-is. This
    makes approval idempotent: approving the same snapshot twice does not
    create a duplicate.

    If an active approval exists for the same application_id but a
    DIFFERENT snapshot_hash, the old approval is revoked first (the user
    is approving a new snapshot, so the old one is stale).
    """
    # Check for an existing active approval for the exact same snapshot.
    stmt = select(SubmissionApprovalRow).where(
        SubmissionApprovalRow.application_id == application_id,
        SubmissionApprovalRow.snapshot_hash == snapshot.snapshot_hash,
        SubmissionApprovalRow.consumed_at.is_(None),
        SubmissionApprovalRow.revoked_at.is_(None),
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "[%s] approval already exists for snapshot %s: id=%s",
            application_id[:12],
            snapshot.snapshot_hash[:12],
            existing.approval_id[:12],
        )
        return existing

    # Revoke any other active approvals for this application (different
    # snapshot hash). The user is approving a new snapshot, so the old
    # approval is stale.
    old_stmt = select(SubmissionApprovalRow).where(
        SubmissionApprovalRow.application_id == application_id,
        SubmissionApprovalRow.snapshot_hash != snapshot.snapshot_hash,
        SubmissionApprovalRow.consumed_at.is_(None),
        SubmissionApprovalRow.revoked_at.is_(None),
    )
    for old_row in session.execute(old_stmt).scalars().all():
        old_row.revoked_at = _utcnow()
        logger.info(
            "[%s] revoked stale approval %s (old snapshot %s)",
            application_id[:12],
            old_row.approval_id[:12],
            old_row.snapshot_hash[:12],
        )

    approval_id = _make_id(application_id, snapshot.snapshot_hash, "approval")
    row = SubmissionApprovalRow(
        approval_id=approval_id,
        application_id=application_id,
        snapshot_hash=snapshot.snapshot_hash,
        snapshot_json=snapshot.model_dump(mode="json"),
        created_at=_utcnow(),
        consumed_at=None,
        revoked_at=None,
    )
    session.add(row)
    session.flush()
    logger.info(
        "[%s] approval created: id=%s snapshot=%s",
        application_id[:12],
        approval_id[:12],
        snapshot.snapshot_hash[:12],
    )
    return row


def get_active_approval(
    session: Session,
    application_id: str,
) -> SubmissionApprovalRow | None:
    """Return the active (non-consumed, non-revoked) approval for an
    application, or None."""
    stmt = select(SubmissionApprovalRow).where(
        SubmissionApprovalRow.application_id == application_id,
        SubmissionApprovalRow.consumed_at.is_(None),
        SubmissionApprovalRow.revoked_at.is_(None),
    )
    return session.execute(stmt).scalar_one_or_none()


def get_approval(
    session: Session,
    approval_id: str,
) -> SubmissionApprovalRow | None:
    """Return an approval by ID (any state)."""
    return session.get(SubmissionApprovalRow, approval_id)


def revoke_approval(session: Session, approval_id: str) -> SubmissionApprovalRow | None:
    """Revoke an approval. Returns the updated row, or None if not found.

    A consumed approval cannot be revoked (it was already used for a
    submit click).
    """
    row = session.get(SubmissionApprovalRow, approval_id)
    if row is None:
        return None
    if row.consumed_at is not None:
        logger.warning(
            "[%s] cannot revoke consumed approval %s",
            row.application_id[:12],
            approval_id[:12],
        )
        return row
    row.revoked_at = _utcnow()
    session.flush()
    logger.info(
        "[%s] approval revoked: id=%s",
        row.application_id[:12],
        approval_id[:12],
    )
    return row


# ---------------------------------------------------------------------------
# Claim management (transactional one-time lock)
# ---------------------------------------------------------------------------


def acquire_claim(
    session: Session,
    *,
    application_id: str,
    approval: SubmissionApprovalRow,
) -> SubmissionClaimRow | None:
    """Acquire a one-time submission claim.

    Returns the claim row if acquired, or None if a claim already exists
    for this approval (preventing duplicate clicks).

    Database-enforced uniqueness: the ``submission_claims`` table has a
    UNIQUE constraint on ``approval_id``. If two concurrent requests try
    to acquire a claim for the same approval, only one INSERT succeeds;
    the other gets an :class:`IntegrityError` which is caught and
    translated to ``None``. This is stronger than SELECT-then-INSERT
    because it survives race conditions even under non-serializable
    isolation levels.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    # Check for an existing unconsumed claim (fast path — avoids the
    # IntegrityError round-trip in the common case).
    stmt = select(SubmissionClaimRow).where(
        SubmissionClaimRow.approval_id == approval.approval_id,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        logger.warning(
            "[%s] submission claim already held: id=%s (acquired %s)",
            application_id[:12],
            existing.claim_id[:12],
            existing.acquired_at.isoformat(),
        )
        return None

    claim_id = _make_id(application_id, approval.approval_id, "claim")
    row = SubmissionClaimRow(
        claim_id=claim_id,
        application_id=application_id,
        approval_id=approval.approval_id,
        snapshot_hash=approval.snapshot_hash,
        acquired_at=_utcnow(),
        consumed_at=None,
        consumed_state=None,
    )
    session.add(row)
    try:
        session.flush()
    except SAIntegrityError:
        # A concurrent request inserted a claim for the same approval
        # between our SELECT and INSERT. The unique constraint on
        # approval_id caught the race. Roll back the flush and return
        # None — the caller must abort.
        session.rollback()
        logger.warning(
            "[%s] submission claim race: concurrent insert detected for approval %s",
            application_id[:12],
            approval.approval_id[:12],
        )
        return None

    logger.info(
        "[%s] submission claim acquired: id=%s",
        application_id[:12],
        claim_id[:12],
    )
    return row


def consume_claim(
    session: Session,
    claim_id: str,
    *,
    state: SubmissionResultState,
) -> SubmissionClaimRow | None:
    """Mark a claim as consumed with the given result state.

    This releases the lock but records the outcome so future attempts
    can see what happened.
    """
    row = session.get(SubmissionClaimRow, claim_id)
    if row is None:
        return None
    row.consumed_at = _utcnow()
    row.consumed_state = str(state)
    session.flush()
    logger.info(
        "[%s] submission claim consumed: id=%s state=%s",
        row.application_id[:12],
        claim_id[:12],
        state,
    )
    return row


def has_unconsumed_claim(session: Session, application_id: str) -> bool:
    """Check if an unconsumed claim exists for this application."""
    stmt = select(SubmissionClaimRow).where(
        SubmissionClaimRow.application_id == application_id,
        SubmissionClaimRow.consumed_at.is_(None),
    )
    return session.execute(stmt).first() is not None


def has_consumed_claim(session: Session, application_id: str) -> bool:
    """Check if a consumed claim exists for this application (a previous
    submit attempt was made)."""
    stmt = select(SubmissionClaimRow).where(
        SubmissionClaimRow.application_id == application_id,
        SubmissionClaimRow.consumed_at.is_not(None),
    )
    return session.execute(stmt).first() is not None


# ---------------------------------------------------------------------------
# Result recording
# ---------------------------------------------------------------------------


def record_result(
    session: Session,
    result: SubmissionResult,
) -> SubmissionResultRow:
    """Persist a submission result for audit.

    Database-enforced idempotency: the ``submission_results`` table has
    a UNIQUE constraint on ``approval_id``. If two concurrent requests
    try to record a result for the same approval, only one INSERT
    succeeds; the other gets an :class:`IntegrityError` which is caught
    and the existing row is returned.
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    stmt = select(SubmissionResultRow).where(
        SubmissionResultRow.approval_id == result.approval_id,
    )
    existing = session.execute(stmt).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "[%s] result already recorded for approval %s",
            result.application_id[:12],
            result.approval_id[:12],
        )
        return existing

    result_id = _make_id(result.application_id, result.approval_id, "result")
    row = SubmissionResultRow(
        result_id=result_id,
        application_id=result.application_id,
        approval_id=result.approval_id,
        snapshot_hash_at_submit=result.snapshot_hash_at_submit,
        state=str(result.state),
        clicked=result.clicked,
        pre_submit_screenshot=result.pre_submit_screenshot,
        post_submit_screenshot=result.post_submit_screenshot,
        post_submit_url=result.post_submit_url or "",
        post_submit_dom_path=result.post_submit_dom_path,
        confirmation_evidence=result.confirmation_evidence,
        validation_errors_json=list(result.validation_errors),
        error_message=result.error_message,
        attempted_at=result.attempted_at,
    )
    session.add(row)
    try:
        session.flush()
    except SAIntegrityError:
        # A concurrent request inserted a result for the same approval.
        # Roll back and return the existing row.
        session.rollback()
        existing = session.execute(stmt).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "[%s] result race resolved: returning existing for approval %s",
                result.application_id[:12],
                result.approval_id[:12],
            )
            return existing
        # If still None after rollback, re-raise (shouldn't happen).
        raise

    logger.info(
        "[%s] submission result recorded: state=%s clicked=%s",
        result.application_id[:12],
        result.state,
        result.clicked,
    )
    return row


def get_latest_result(
    session: Session,
    application_id: str,
) -> SubmissionResultRow | None:
    """Return the most recent submission result for an application."""
    stmt = (
        select(SubmissionResultRow)
        .where(SubmissionResultRow.application_id == application_id)
        .order_by(SubmissionResultRow.attempted_at.desc())
    )
    return session.execute(stmt).scalars().first()


# ---------------------------------------------------------------------------
# Approval consumption (marks the approval as used)
# ---------------------------------------------------------------------------


def consume_approval(
    session: Session,
    approval_id: str,
) -> SubmissionApprovalRow | None:
    """Mark an approval as consumed (used for a submit click).

    Once consumed, the approval cannot be reused. This is called after
    the submit click has been attempted (regardless of outcome).
    """
    row = session.get(SubmissionApprovalRow, approval_id)
    if row is None:
        return None
    if row.consumed_at is not None:
        logger.warning(
            "[%s] approval %s already consumed at %s",
            row.application_id[:12],
            approval_id[:12],
            row.consumed_at.isoformat(),
        )
        return row
    row.consumed_at = _utcnow()
    session.flush()
    logger.info(
        "[%s] approval consumed: id=%s",
        row.application_id[:12],
        approval_id[:12],
    )
    return row


# ---------------------------------------------------------------------------
# Helpers for gate checks
# ---------------------------------------------------------------------------


def count_pending_interventions(session: Session, application_id: str) -> int:
    """Count PENDING interventions for an application."""
    return len(list_pending_interventions(session, application_id))


def approval_to_model(row: SubmissionApprovalRow) -> SubmissionApproval:
    """Convert a DB row to a Pydantic model."""
    return SubmissionApproval(
        approval_id=row.approval_id,
        application_id=row.application_id,
        snapshot_hash=row.snapshot_hash,
        created_at=row.created_at,
        consumed_at=row.consumed_at,
        revoked_at=row.revoked_at,
    )


def claim_to_model(row: SubmissionClaimRow) -> SubmissionClaim:
    """Convert a DB row to a Pydantic model."""
    return SubmissionClaim(
        claim_id=row.claim_id,
        application_id=row.application_id,
        approval_id=row.approval_id,
        snapshot_hash=row.snapshot_hash,
        acquired_at=row.acquired_at,
        consumed_at=row.consumed_at,
        consumed_state=row.consumed_state or "",
    )


__all__ = [
    "acquire_claim",
    "approval_to_model",
    "build_snapshot",
    "claim_to_model",
    "consume_approval",
    "consume_claim",
    "count_pending_interventions",
    "create_approval",
    "get_active_approval",
    "get_approval",
    "get_latest_result",
    "has_consumed_claim",
    "has_unconsumed_claim",
    "record_result",
    "revoke_approval",
]
