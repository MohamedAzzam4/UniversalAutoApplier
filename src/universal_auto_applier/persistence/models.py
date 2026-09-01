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
    ats_reference_id: Mapped[str] = mapped_column(Text, default="")
    validation_errors_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SubmissionAuthorizationRow(Base):
    """A single-use, expiry-bound real-submission authorization (WQ-8).

    The stricter authorization that must exist and be active for a real
    submission to proceed. Bound to the application, the job identity, the
    target URL, the frozen ``review_plan_hash`` and the CV/document content
    hashes. Consumed compare-and-set as submission initiates. There is at
    most one total (see the authorization store's absolute-limit enforcement).
    """

    __tablename__ = "submission_authorizations"

    authorization_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("application_jobs.application_id"),
        index=True,
    )
    application_url: Mapped[str] = mapped_column(Text, default="")
    job_company: Mapped[str] = mapped_column(String(256), default="")
    job_title: Mapped[str] = mapped_column(String(256), default="")
    review_plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    document_hashes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job: Mapped[ApplicationJobRow] = relationship()


class QueueImportRunRow(Base):
    """One durable run of the queue-import service.

    Recorded for every import attempt so history survives restart. Row errors
    are stored as structured ``{line_number, error}`` pairs — the raw JSONL
    line is never persisted here, because it may carry candidate data.
    """

    __tablename__ = "queue_import_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_path: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(String(16))
    # sha256 hex digest of the source file content; None when unreadable.
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # success | partial | failed | skipped
    state: Mapped[str] = mapped_column(String(16), index=True)
    total_lines: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    row_errors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineRunRow(Base):
    """One durable pipeline run (WQ-4 background browser pipeline).

    The authoritative, restart-safe snapshot of a pipeline run. The runtime
    ``PipelineWorkerService`` persists every mutation of a run into this table
    and reads status back from it, so an API restart keeps the run id, status,
    progress counts, current job, error history, timestamps, and cancellation
    reason visible. A worker subprocess performs the actual browser work and
    writes progress into the same row.

    Status values: idle, running, pausing, paused, cancelling, cancelled,
    completed, failed, recovered.
    """

    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # idle | running | pausing | paused | cancelling | cancelled | completed | failed | recovered
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(64), default="sequential_dry_run")
    current_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_phase: Mapped[str] = mapped_column(String(64), default="")
    last_action: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    jobs_total: Mapped[int] = mapped_column(Integer, default=0)
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_skipped: Mapped[int] = mapped_column(Integer, default=0)
    # Structured per-job errors: {"timestamp", "application_id", "error", "phase"}.
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # WQ-5 worker liveness: who owns this run and how recently it proved it
    # is alive. Used by startup recovery to distinguish a healthy worker from a
    # stale run (missing/dead pid AND expired/missing heartbeat).
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchestrationRunRow(Base):
    """One durable cross-repository orchestration run (WQ-6).

    Persists the full lifecycle of a JobHunter-export -> UAA-import ->
    UAA-pipeline run so it survives API/server restart. Only one active
    orchestration run may exist at a time (enforced by the service layer).
    The row never stores secrets; the JobHunter subprocess stdout/stderr is
    captured with bounded storage and filtered by the service before being
    written here.
    """

    __tablename__ = "orchestration_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # sequential | parallel
    mode: Mapped[str] = mapped_column(String(16), index=True)
    # WQ-7C: True only when the operator explicitly opted into a synthetic
    # orchestration run. Synthetic runs import a pre-produced synthetic queue
    # (WQ-7C synthetic markers propagated by the queue-import service, normal
    # candidate data rejected), never re-run the production JobHunter
    # workflow, target only the newly imported application IDs, and are
    # incompatible with real submission.
    synthetic_orchestration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # idle | running | jobhunter_running | importing | pipeline_running
    # | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_phase: Mapped[str] = mapped_column(String(64), default="")
    last_action: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    # JobHunter child process liveness
    jobhunter_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jobhunter_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    jobhunter_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    jobhunter_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bounded stdout/stderr capture (secrets filtered by the service)
    jobhunter_stdout: Mapped[str] = mapped_column(Text, default="")
    jobhunter_stderr: Mapped[str] = mapped_column(Text, default="")
    # Queue import result
    queue_import_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_import_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    queue_imported: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    queue_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # UAA pipeline run links. In sequential mode, only pipeline_run_id is
    # used. In parallel mode, pipeline_run_id_initial is the first pass
    # (existing jobs) and pipeline_run_id is the second pass (newly imported
    # jobs). pipeline_state / pipeline_state_initial track their respective
    # terminal states.
    pipeline_run_id_initial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_state_initial: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Queue publication detection: content hash + mtime_ns before/after.
    queue_hash_before: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_hash_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_mtime_ns_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_mtime_ns_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_published: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Exact newly eligible evidence: count + list of application_id hashes.
    newly_eligible_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    newly_eligible_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Durable batch-evidence (WQ-6 round 7). These columns are updated after
    # every batch in the multi-batch continuation loop so the orchestration
    # state remains truthful after restart or failure. ``targeted_ids_json``
    # is set once when the loop starts; the other columns are updated as
    # batches complete. All lists contain only application_id hashes (never
    # candidate data) and are bounded by the number of newly imported jobs.
    targeted_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    processed_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    remaining_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    targeted_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    processed_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    remaining_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    pipeline_run_ids_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    pass_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    # Bounded structured errors
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupervisorRunRow(Base):
    """One bounded AI-supervisor run (agent-assisted operator mode V0).

    A supervisor run imports/reads the application queue and drives
    review-only preparation through the safe typed tool layer. The row is
    the durable summary record; per-decision history lives in
    ``supervisor_events``. A run NEVER submits: there is no submitted
    transition anywhere in the supervisor state machine.
    """

    __tablename__ = "supervisor_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # running | completed | failed
    status: Mapped[str] = mapped_column(String(16), index=True)
    queue_path: Mapped[str] = mapped_column(Text, default="")
    # V0 is always review-only; the column exists so a future audit can
    # prove no run ever had another mode.
    review_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupervisorApplicationStateRow(Base):
    """Latest supervisor state for one application.

    One row per application (upserted). The full per-action history is in
    ``supervisor_events``; this row answers "where is this application in
    the supervisor pipeline right now?".
    """

    __tablename__ = "supervisor_application_states"

    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    # IMPORTED | PREPARING | RUNNING | WAITING_FOR_INTERVENTION |
    # RETRY_PENDING | REVIEW_READY | NEEDS_HUMAN | REPAIR_NEEDED |
    # BLOCKED | SKIPPED | FAILED — no SUBMITTED state exists in V0.
    state: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), default="")
    decision_source: Mapped[str] = mapped_column(String(32), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SupervisorEventRow(Base):
    """One auditable supervisor action (the "why did the agent do this?" log).

    Every supervisor mutation of an application is recorded here with the
    previous/resulting state, the reason code, the decision source and the
    tool result. Sensitive answer values are never stored — ``detail_json``
    carries redacted metadata (e.g. ``value_redacted: true``) instead.
    """

    __tablename__ = "supervisor_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(64), index=True)
    previous_state: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(48))
    resulting_state: Mapped[str] = mapped_column(String(32), default="")
    reason_code: Mapped[str] = mapped_column(String(64), default="")
    decision_source: Mapped[str] = mapped_column(String(32), default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_result: Mapped[str] = mapped_column(Text, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class HumanHandoffRow(Base):
    """A structured task for the human owner created by the supervisor.

    Created whenever the supervisor hits a condition only a human may
    decide (CAPTCHA, 2FA, unknown salary, sensitive consent, ...). Sanitized
    by design: no full candidate PII, no raw documents, no filled values.
    """

    __tablename__ = "human_handoffs"

    handoff_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(64), index=True)
    company: Mapped[str] = mapped_column(String(256), default="")
    role: Mapped[str] = mapped_column(String(256), default="")
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    action_required: Mapped[str] = mapped_column(Text, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # open | resolved
    status: Mapped[str] = mapped_column(String(16), index=True, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RepairTicketRow(Base):
    """A sanitized implementation-defect report created by the supervisor.

    The supervisor never modifies production code while an attempt is
    running; suspected software defects (e.g. a mapper that cannot resolve a
    field whose fact exists in the candidate profile) are reported here for
    a later human/coding-agent review. Never contains candidate PII, raw CV
    content, credentials, cookies, or session tokens.
    """

    __tablename__ = "repair_tickets"

    ticket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    application_id: Mapped[str] = mapped_column(String(64), index=True)
    ats_family: Mapped[str] = mapped_column(String(64), default="")
    page_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    field_label: Mapped[str] = mapped_column(String(256), default="")
    field_type: Mapped[str] = mapped_column(String(32), default="")
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    expected_source: Mapped[str] = mapped_column(Text, default="")
    actual_failure: Mapped[str] = mapped_column(Text, default="")
    selector_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retry_history_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    suggested_reproduction: Mapped[str] = mapped_column(Text, default="")
    # open | resolved
    status: Mapped[str] = mapped_column(String(16), index=True, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


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
    "QueueImportRunRow",
    "PipelineRunRow",
    "OrchestrationRunRow",
    "SupervisorRunRow",
    "SupervisorApplicationStateRow",
    "SupervisorEventRow",
    "HumanHandoffRow",
    "RepairTicketRow",
]
