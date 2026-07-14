"""Finite status enumerations used across the application.

These mirror the allowed values defined in ``docs/generalization/DATA_CONTRACTS.md``.
Keeping them in one place prevents drift between the API, persistence, and
adapter layers.

The bootstrap phase only needs the application status enum for the health
endpoint's contract test. The other enums (attempt modes, adapter result
statuses, phases, intervention kinds, etc.) are declared here so that later
phases do not need to relocate them.
"""

from __future__ import annotations

from enum import StrEnum


class ApplicationStatus(StrEnum):
    """Lifecycle states for an :class:`ApplicationJob`."""

    DISCOVERED = "discovered"
    EVALUATED = "evaluated"
    REJECTED = "rejected"
    TAILORED = "tailored"
    READY_TO_APPLY = "ready_to_apply"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    NEEDS_USER_INPUT = "needs_user_input"
    REVIEW_READY = "review_ready"
    SUBMITTED = "submitted"
    NEEDS_REVIEW = "needs_review"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"
    CLOSED = "closed"
    BLOCKED = "blocked"


TERMINAL_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.APPLIED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.SKIPPED,
        ApplicationStatus.CLOSED,
    }
)


# Expected transitions per DATA_CONTRACTS.md -> Application Status Lifecycle.
# Used by store-level guards in later phases; declared here so it has a single
# home.
ALLOWED_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DISCOVERED: frozenset({ApplicationStatus.EVALUATED}),
    ApplicationStatus.EVALUATED: frozenset(
        {ApplicationStatus.REJECTED, ApplicationStatus.TAILORED}
    ),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.TAILORED: frozenset({ApplicationStatus.READY_TO_APPLY}),
    ApplicationStatus.READY_TO_APPLY: frozenset(
        {ApplicationStatus.QUEUED, ApplicationStatus.IN_PROGRESS}
    ),
    ApplicationStatus.QUEUED: frozenset({ApplicationStatus.IN_PROGRESS, ApplicationStatus.SKIPPED}),
    ApplicationStatus.IN_PROGRESS: frozenset(
        {
            ApplicationStatus.NEEDS_USER_INPUT,
            ApplicationStatus.REVIEW_READY,
            ApplicationStatus.FAILED,
        }
    ),
    ApplicationStatus.NEEDS_USER_INPUT: frozenset({ApplicationStatus.IN_PROGRESS}),
    ApplicationStatus.REVIEW_READY: frozenset({ApplicationStatus.SUBMITTED}),
    ApplicationStatus.SUBMITTED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.NEEDS_REVIEW}
    ),
    ApplicationStatus.NEEDS_REVIEW: frozenset({ApplicationStatus.QUEUED}),
    ApplicationStatus.APPLIED: frozenset(),
    ApplicationStatus.FAILED: frozenset({ApplicationStatus.QUEUED}),
    ApplicationStatus.BLOCKED: frozenset({ApplicationStatus.QUEUED}),
    ApplicationStatus.SKIPPED: frozenset(),
    ApplicationStatus.CLOSED: frozenset(),
}


class AttemptMode(StrEnum):
    """How an :class:`ApplicationAttempt` is allowed to behave."""

    DRY_RUN = "dry_run"
    REVIEW = "review"
    TRUSTED_AUTO_SUBMIT = "trusted_auto_submit"


class AdapterResultStatus(StrEnum):
    """Statuses returned by :class:`ApplicationAdapter` methods."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    NEEDS_USER_INPUT = "needs_user_input"
    REVIEW_READY = "review_ready"
    SUBMITTED = "submitted"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class Phase(StrEnum):
    """Phases an attempt can move through."""

    PREPARE = "prepare"
    NAVIGATE = "navigate"
    OBSERVE = "observe"
    FILL = "fill"
    REVIEW = "review"
    SUBMIT = "submit"
    VERIFY = "verify"
    CLEANUP = "cleanup"


class Platform(StrEnum):
    """Platform identifiers per ``DATA_CONTRACTS.md``."""

    SIEMENS = "siemens"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    SMARTRECRUITERS = "smartrecruiters"
    LINKEDIN_EASY_APPLY = "linkedin_easy_apply"
    GENERIC = "generic"
    UNKNOWN = "unknown"


class PageState(StrEnum):
    """States reported by :class:`PageObserver` in Phase 3."""

    JOB_PAGE = "job_page"
    APPLY_PAGE = "apply_page"
    LOGIN = "login"
    REGISTER = "register"
    FORM = "form"
    SCREENING_QUESTIONS = "screening_questions"
    REVIEW = "review"
    SUBMITTED = "submitted"
    CAPTCHA = "captcha"
    EXPIRED = "expired"
    ERROR = "error"
    UNKNOWN = "unknown"


class ClickableClassification(StrEnum):
    SAFE_APPLY = "safe_apply"
    SAFE_CONTINUE = "safe_continue"
    SAFE_UPLOAD = "safe_upload"
    DANGEROUS_SUBMIT = "dangerous_submit"
    LOGIN = "login"
    EXTERNAL_LINK = "external_link"
    UNKNOWN = "unknown"


class InterventionKind(StrEnum):
    FIELD_ANSWER = "field_answer"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    UNKNOWN_PAGE = "unknown_page"
    REVIEW_BEFORE_SUBMIT = "review_before_submit"
    MISSING_DOCUMENT = "missing_document"
    VALIDATION_ERROR = "validation_error"
    MANUAL_UPLOAD_REQUIRED = "manual_upload_required"


class InterventionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    RESOLVED = "resolved"


class HealthState(StrEnum):
    """Top-level health states reported by the dashboard."""

    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
