"""WQ-8 authorization store — persisted single-use authorizations.

All mutations go through this store. Enforcement rules:

- **Absolute limit:** an authorization is only creatable when no consumed
  real-submission attempt exists anywhere (``submission_results`` with
  ``clicked=true`` in a clicked-attempt state) AND no active authorization
  already exists for a different application. There can be at most one
  real-submission authorization across the whole system, for one application.
- **Idempotent creation:** re-creating the identical (application_id,
  review_plan_hash) authorization returns the existing row.
- **Single-use consumption:** ``consume_authorization`` is compare-and-set
  (only when not already consumed) so a concurrent second consumer loses.
- **Revocation** invalidates an unconsumed authorization (owner can revoke a
  planned authorization before Phase B).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from universal_auto_applier.persistence.models import (
    SubmissionAuthorizationRow,
    SubmissionResultRow,
)
from universal_auto_applier.submission.authorization import (
    CLICKED_ATTEMPT_STATES,
    SubmissionAuthorization,
    make_authorization_id,
)

logger = logging.getLogger("universal_auto_applier.submission.authorization_store")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def has_converted_submission(session: Session) -> bool:
    """True when any job already records a clicked real-submission attempt.

    This is the durable guard for the absolute one-submission limit: once any
    real submission has been attempted/consumed, no new authorization can ever
    be created.
    """
    stmt = select(SubmissionResultRow).where(
        SubmissionResultRow.clicked.is_(True),
        SubmissionResultRow.state.in_(sorted(CLICKED_ATTEMPT_STATES)),
    )
    return session.execute(stmt).first() is not None


def count_clicked_attempts(session: Session) -> int:
    """Number of clicked real-submission attempts recorded anywhere."""
    stmt = select(SubmissionResultRow).where(
        SubmissionResultRow.clicked.is_(True),
        SubmissionResultRow.state.in_(sorted(CLICKED_ATTEMPT_STATES)),
    )
    return len(session.execute(stmt).scalars().all())


def create_authorization(
    session: Session,
    *,
    application_id: str,
    application_url: str,
    job_company: str,
    job_title: str,
    review_plan_hash: str,
    document_hashes: list[str],
    expires_at: datetime,
) -> SubmissionAuthorizationRow:
    """Create a single-use authorization (owner-only Phase B step).

    Refuses (raises ``ValueError``) when:
    - A clicked real-submission attempt already exists anywhere (absolute
      one-submission limit consumed).
    - An active authorization exists for a DIFFERENT application (there can
      be at most one real-submission authorization).
    - ``expires_at`` is not in the future.

    Creating the identical (application_id, review_plan_hash) authorization
    again is idempotent: the existing row is returned.
    """
    if expires_at <= _utcnow():
        raise ValueError("expires_at must be in the future")

    authorization_id = make_authorization_id(application_id, review_plan_hash)

    existing = session.get(SubmissionAuthorizationRow, authorization_id)
    if existing is not None:
        logger.info(
            "[%s] authorization already exists: id=%s rev=%s",
            application_id[:12],
            existing.authorization_id[:12],
            str(existing.revoked_at),
        )
        return existing

    if has_converted_submission(session):
        raise ValueError(
            "absolute real-submission limit reached: a clicked real "
            "submission attempt already exists for another job"
        )

    other_stmt = select(SubmissionAuthorizationRow).where(
        SubmissionAuthorizationRow.application_id != application_id,
        SubmissionAuthorizationRow.consumed_at.is_(None),
        SubmissionAuthorizationRow.revoked_at.is_(None),
    )
    other = session.execute(other_stmt).scalar_one_or_none()
    if other is not None:
        raise ValueError(
            f"an active authorization already exists for "
            f"{other.application_id[:12]} (one authorization total); "
            f"no second real application is allowed"
        )

    row = SubmissionAuthorizationRow(
        authorization_id=authorization_id,
        application_id=application_id,
        application_url=application_url,
        job_company=job_company,
        job_title=job_title,
        review_plan_hash=review_plan_hash,
        document_hashes_json=list(document_hashes),
        created_at=_utcnow(),
        consumed_at=None,
        revoked_at=None,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    logger.info(
        "[%s] WQ-8 authorization created: id=%s plan=%s expires=%s",
        application_id[:12],
        authorization_id[:12],
        review_plan_hash[:12],
        expires_at.isoformat(),
    )
    return row


def get_authorization(
    session: Session,
    authorization_id: str,
) -> SubmissionAuthorizationRow | None:
    """Return an authorization by ID (any state)."""
    return session.get(SubmissionAuthorizationRow, authorization_id)


def get_active_authorization(
    session: Session,
    application_id: str,
) -> SubmissionAuthorizationRow | None:
    """Return the active (non-consumed, non-revoked, not-expired)
    authorization for an application, or None."""
    from sqlalchemy import and_

    stmt = select(SubmissionAuthorizationRow).where(
        and_(
            SubmissionAuthorizationRow.application_id == application_id,
            SubmissionAuthorizationRow.consumed_at.is_(None),
            SubmissionAuthorizationRow.revoked_at.is_(None),
            SubmissionAuthorizationRow.expires_at > _utcnow(),
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def get_authorization_for_plan(
    session: Session,
    application_id: str,
    review_plan_hash: str,
) -> SubmissionAuthorizationRow | None:
    """Return the authorization row for (app, plan), any state."""
    stmt = select(SubmissionAuthorizationRow).where(
        SubmissionAuthorizationRow.application_id == application_id,
        SubmissionAuthorizationRow.review_plan_hash == review_plan_hash,
    )
    return session.execute(stmt).scalar_one_or_none()


def revoke_authorization(
    session: Session,
    authorization_id: str,
) -> SubmissionAuthorizationRow | None:
    """Revoke an unconsumed authorization. A consumed authorization cannot be
    revoked (it already drove a submit click)."""
    row = session.get(SubmissionAuthorizationRow, authorization_id)
    if row is None:
        return None
    if row.consumed_at is not None:
        logger.warning(
            "[%s] cannot revoke consumed authorization %s",
            row.application_id[:12],
            authorization_id[:12],
        )
        return row
    row.revoked_at = _utcnow()
    session.flush()
    logger.info(
        "[%s] WQ-8 authorization revoked: id=%s",
        row.application_id[:12],
        authorization_id[:12],
    )
    return row


def consume_authorization(session: Session, authorization_id: str) -> bool:
    """Consume an authorization (compare-and-set, single-use).

    Returns True if this call transitioned the row to consumed. Returns False
    when the row is already consumed/revoked/missing (a concurrent or second
    consumer loses; the submit must not proceed with a lost race).
    """
    now = _utcnow()
    stmt = (
        update(SubmissionAuthorizationRow)
        .where(
            SubmissionAuthorizationRow.authorization_id == authorization_id,
            SubmissionAuthorizationRow.consumed_at.is_(None),
            SubmissionAuthorizationRow.revoked_at.is_(None),
            SubmissionAuthorizationRow.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    result = session.execute(stmt)
    session.flush()
    rowcount = getattr(result, "rowcount", 0)
    if rowcount == 1:
        logger.info("[%s] WQ-8 authorization consumed", authorization_id[:12])
        return True
    row = session.get(SubmissionAuthorizationRow, authorization_id)
    if row is not None:
        logger.warning(
            "[%s] authorization not consumable (consumed=%s revoked=%s expired=%s)",
            row.application_id[:12],
            row.consumed_at,
            row.revoked_at,
            row.expires_at,
        )
    return False


def authorization_to_model(row: SubmissionAuthorizationRow) -> SubmissionAuthorization:
    """Convert a DB row to a Pydantic model."""
    return SubmissionAuthorization(
        authorization_id=row.authorization_id,
        application_id=row.application_id,
        application_url=row.application_url,
        job_company=row.job_company,
        job_title=row.job_title,
        review_plan_hash=row.review_plan_hash,
        document_hashes=list(row.document_hashes_json or []),
        created_at=row.created_at,
        consumed_at=row.consumed_at,
        revoked_at=row.revoked_at,
        expires_at=row.expires_at,
    )


__all__ = [
    "authorization_to_model",
    "consume_authorization",
    "count_clicked_attempts",
    "create_authorization",
    "get_active_authorization",
    "get_authorization",
    "get_authorization_for_plan",
    "has_converted_submission",
    "revoke_authorization",
]
