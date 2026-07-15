"""SQLAlchemy ORM models for the required database tables.

The schema follows ``TECHNICAL_BASELINE.md`` -> Required database tables:

    application_jobs
    application_attempts
    phase_results
    interventions
    answer_memories
    artifacts
    system_runs

This is a bootstrap scaffold: columns match the data contracts documented in
``DATA_CONTRACTS.md``, but no business logic, repositories, or query helpers
live here yet. They will be added in later phases alongside store methods.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    """Return timezone-aware UTC ``now`` for default factories."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class ApplicationJobRow(Base):
    """A normalized job imported from the JobHunter queue."""

    __tablename__ = "application_jobs"

    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    company: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    job_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(32))
    cv_pdf: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_pdf: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    # Optional identity fields, needed to recompute application_id on read.
    job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_job_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    # Optional descriptive fields from the ApplicationJob contract.
    date_posted: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tailored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    german_filter_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    documents_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    attempts: Mapped[list[ApplicationAttemptRow]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    interventions: Mapped[list[InterventionRow]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    submission_approvals: Mapped[list[SubmissionApprovalRow]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class ApplicationAttemptRow(Base):
    """One processing run for an :class:`ApplicationJobRow`."""

    __tablename__ = "application_attempts"

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submit_approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job: Mapped[ApplicationJobRow] = relationship(back_populates="attempts")
    phase_results: Mapped[list[PhaseResultRow]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="PhaseResultRow.sequence",
    )
    artifacts: Mapped[list[ArtifactRow]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class PhaseResultRow(Base):
    """Immutable per-phase outcome appended to an attempt."""

    __tablename__ = "phase_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_attempts.attempt_id"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    attempt: Mapped[ApplicationAttemptRow] = relationship(back_populates="phase_results")


class InterventionRow(Base):
    """A user-facing task asking for approval or manual input."""

    __tablename__ = "interventions"

    intervention_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    suggested_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[ApplicationJobRow] = relationship(back_populates="interventions")


class AnswerMemoryRow(Base):
    """A user-confirmed answer keyed by normalized question pattern."""

    __tablename__ = "answer_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_question: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    answer: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class ArtifactRow(Base):
    """Evidence file (screenshot, trace, document) attached to an attempt."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attempt_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_attempts.attempt_id"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    attempt: Mapped[ApplicationAttemptRow] = relationship(back_populates="artifacts")


class SystemRunRow(Base):
    """One execution of the local system (process lifetime)."""

    __tablename__ = "system_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_mode: Mapped[str] = mapped_column(String(32))
    headless: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SubmissionApprovalRow(Base):
    """A one-time approval for a specific form snapshot.

    Tied to (application_id, snapshot_hash). Changing the form state
    produces a new snapshot hash, invalidating this approval. Consumed
    by a :class:`SubmissionClaimRow` when a submit click is attempted.
    """

    __tablename__ = "submission_approvals"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confirmed_high_risk_fields_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[ApplicationJobRow] = relationship(back_populates="submission_approvals")


class SubmissionClaimRow(Base):
    """A transactional one-time lock preventing duplicate submit clicks.

    Acquired BEFORE the click, consumed AFTER the outcome is recorded.
    If the process crashes between acquisition and consumption, the claim
    remains held and blocks automatic retry.
    """

    __tablename__ = "submission_claims"

    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("submission_approvals.approval_id"),
        index=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_state: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SubmissionResultRow(Base):
    """The persisted outcome of one submission attempt.

    Every click attempt is recorded here for audit. The unique constraint
    on (application_id, approval_id) ensures one result per approval —
    preventing duplicate audit records from concurrent requests.
    """

    __tablename__ = "submission_results"

    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_hash_at_submit: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(64))
    clicked: Mapped[bool] = mapped_column(Boolean, default=False)
    pre_submit_screenshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_submit_screenshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_submit_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_submit_dom_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_evidence: Mapped[str] = mapped_column(Text, default="")
    validation_errors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = [
    "Base",
    "ApplicationJobRow",
    "ApplicationAttemptRow",
    "PhaseResultRow",
    "InterventionRow",
    "AnswerMemoryRow",
    "ArtifactRow",
    "SystemRunRow",
    "SubmissionApprovalRow",
    "SubmissionClaimRow",
    "SubmissionResultRow",
]
