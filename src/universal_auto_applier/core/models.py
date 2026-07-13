"""Core Pydantic v2 contracts.

This module defines the shared data structures from
``docs/generalization/DATA_CONTRACTS.md``. Every model here is a Pydantic v2
``BaseModel`` and validates input at construction time.

Models implemented in Phase 1:

- :class:`ApplicationJob` — the normalized handoff from JobHunter.
- :class:`ApplicationAttempt` — one processing run for a job.
- :class:`PhaseResult` — an immutable per-phase outcome.
- :class:`AdapterResult` — returned by every adapter method.
- :class:`Intervention` — a user-facing task.
- :class:`AnswerMemory` — a user-confirmed answer.
- :class:`Artifact` — an evidence file.

Models deferred to later phases (PageObservation, Clickable, FormField,
FieldMapping) are documented in DATA_CONTRACTS.md but not yet implemented
here. They land in Phase 3+.

The :class:`HealthReport` and :class:`ComponentHealth` models remain here
from the bootstrap phase because they are used by the health endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from universal_auto_applier import __version__
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
    AttemptMode,
    ClickableClassification,
    HealthState,
    InterventionKind,
    InterventionStatus,
    PageState,
    Phase,
    Platform,
)

# ---------------------------------------------------------------------------
# Health contracts (from bootstrap)
# ---------------------------------------------------------------------------


class ComponentHealth(BaseModel):
    """Health of one capability listed in DEPLOYMENT_AND_REPO_STRATEGY.md."""

    name: str = Field(..., description="Capability name, e.g. 'api', 'store'.")
    state: HealthState
    detail: str = Field(default="", description="Optional human-readable note.")


class HealthReport(BaseModel):
    """Aggregated system health returned by ``GET /api/health``."""

    status: HealthState = Field(
        default=HealthState.READY,
        description="Top-level status. 'ready' only when all required capabilities are ready.",
    )
    version: str = Field(default=__version__)
    components: list[ComponentHealth] = Field(default_factory=list[ComponentHealth])

    def find(self, name: str) -> ComponentHealth | None:
        """Return the component with ``name`` or ``None`` if absent."""
        for component in self.components:
            if component.name == name:
                return component
        return None


# ---------------------------------------------------------------------------
# ApplicationJob (Phase 1 WP 1.1)
# ---------------------------------------------------------------------------


class ApplicationJobDocuments(BaseModel):
    """Optional document artifact paths (markdown sources)."""

    cv_md: str | None = Field(default=None, description="Absolute path to tailored CV markdown.")
    cover_letter_md: str | None = Field(
        default=None, description="Absolute path to tailored cover letter markdown."
    )


class ApplicationJob(BaseModel):
    """The normalized handoff from JobHunter to the applier.

    See ``DATA_CONTRACTS.md`` -> ``ApplicationJob`` for the full contract.
    """

    # --- Required fields ---
    application_id: str = Field(
        ..., min_length=64, max_length=64, description="Lowercase SHA-256 hexdigest."
    )
    platform: Platform
    source: str = Field(..., min_length=1, description="Job source, e.g. 'linkedin'.")
    company: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    url: str = Field(..., description="HTTP or HTTPS URL.")
    location: str | None = Field(default=None)
    job_description: str | None = Field(default=None)
    score: float | None = Field(default=None, ge=0.0)
    verdict: str = Field(..., description="One of: apply, consider, skip.")
    cv_pdf: str | None = Field(default=None, description="Absolute path to tailored CV PDF.")
    cover_letter_pdf: str | None = Field(
        default=None, description="Absolute path to tailored cover letter PDF."
    )
    status: ApplicationStatus

    # --- Optional fields ---
    job_id: str | None = Field(default=None, description="Platform-specific ID if known.")
    external_job_id: str | None = Field(default=None, description="ID from the source.")
    date_posted: str | None = Field(
        default=None, description="ISO date YYYY-MM-DD.", pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    evaluated_at: datetime | None = Field(default=None)
    tailored_at: datetime | None = Field(default=None)
    evaluation_reason: str | None = Field(default=None)
    german_filter_result: str | None = Field(default=None)
    documents: ApplicationJobDocuments | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])

    model_config = {"extra": "allow"}

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        """Reject non-HTTP(S) URLs."""
        from urllib.parse import urlsplit

        parts = urlsplit(v)
        if parts.scheme.lower() not in ("http", "https"):
            raise ValueError("url must be HTTP or HTTPS")
        if not parts.hostname:
            raise ValueError("url must have a hostname")
        return v

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, v: str) -> str:
        allowed = {"apply", "consider", "skip"}
        if v not in allowed:
            raise ValueError(f"verdict must be one of {allowed}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_documents_required_for_ready(self) -> ApplicationJob:
        """``cv_pdf`` and ``cover_letter_pdf`` must exist when status is
        ``ready_to_apply``.

        Per DATA_CONTRACTS.md: "cv_pdf and cover_letter_pdf must exist before
        status becomes ready_to_apply, unless the platform allows no-document
        applications."

        The platform-exception case is not yet needed (no platform is marked
        no-document in v1), so we enforce the strict rule for now.
        """
        if self.status == ApplicationStatus.READY_TO_APPLY:
            if not self.cv_pdf:
                raise ValueError("cv_pdf is required when status is ready_to_apply")
            if not self.cover_letter_pdf:
                raise ValueError("cover_letter_pdf is required when status is ready_to_apply")
        return self

    @model_validator(mode="after")
    def _validate_application_id(self) -> ApplicationJob:
        """Verify that ``application_id`` matches the deterministic computation.

        This is a soft check: if ``application_id`` is provided, it must match
        the recomputed value. If it does not match, we raise. This catches
        drift between JobHunter's export and the canonical algorithm.
        """
        expected = compute_application_id(
            platform=str(self.platform) if self.platform else None,
            external_job_id=self.external_job_id,
            url=self.url,
        )
        if self.application_id != expected:
            raise ValueError(
                f"application_id {self.application_id!r} does not match "
                f"deterministic value {expected!r} computed from "
                f"platform/external_job_id/url"
            )
        return self

    @classmethod
    def compute_id(
        cls,
        *,
        platform: str | None,
        external_job_id: str | None,
        url: str,
    ) -> str:
        """Convenience: compute the deterministic ``application_id``."""
        return compute_application_id(platform=platform, external_job_id=external_job_id, url=url)


# ---------------------------------------------------------------------------
# ApplicationAttempt (Phase 1)
# ---------------------------------------------------------------------------


class ApplicationAttempt(BaseModel):
    """One processing run for an :class:`ApplicationJob`.

    Every processing run creates a new immutable attempt record. Phase results
    append to an attempt; they are not overwritten.
    """

    attempt_id: str = Field(..., description="UUID generated by UniversalAutoApplier.")
    application_id: str = Field(..., min_length=64, max_length=64)
    run_id: str = Field(..., description="UUID of the system run.")
    adapter: str = Field(..., description="Adapter name, e.g. 'greenhouse'.")
    mode: AttemptMode
    status: ApplicationStatus
    started_at: datetime
    finished_at: datetime | None = Field(default=None)
    last_phase: Phase | None = Field(default=None)
    submit_approval_id: str | None = Field(
        default=None, description="Approval ID, consumed after submit."
    )


# ---------------------------------------------------------------------------
# PhaseResult (Phase 1)
# ---------------------------------------------------------------------------


class PhaseResult(BaseModel):
    """An immutable per-phase outcome appended to an attempt."""

    attempt_id: str
    sequence: int = Field(..., ge=1)
    phase: Phase
    status: AdapterResultStatus
    message: str | None = Field(default=None)
    screenshot: str | None = Field(default=None, description="Path to screenshot file.")
    recorded_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])


# ---------------------------------------------------------------------------
# AdapterResult (Phase 1)
# ---------------------------------------------------------------------------


class AdapterResult(BaseModel):
    """Returned by every :class:`ApplicationAdapter` method.

    A result must be structured even when an exception happens.
    """

    status: AdapterResultStatus
    phase: Phase
    message: str = Field(default="")
    application_id: str | None = Field(default=None)
    platform: Platform | None = Field(default=None)
    next_action: str | None = Field(
        default=None, description="Suggested next action, e.g. 'fill_form'."
    )
    screenshots: list[str] = Field(default_factory=list[str])
    errors: list[str] = Field(default_factory=list[str])
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])

    @classmethod
    def success(
        cls,
        *,
        phase: Phase,
        message: str = "",
        application_id: str | None = None,
        platform: Platform | None = None,
        next_action: str | None = None,
        screenshots: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdapterResult:
        return cls(
            status=AdapterResultStatus.SUCCESS,
            phase=phase,
            message=message,
            application_id=application_id,
            platform=platform,
            next_action=next_action,
            screenshots=screenshots or [],
            metadata=metadata or {},
        )

    @classmethod
    def failed(
        cls,
        *,
        phase: Phase,
        message: str = "",
        application_id: str | None = None,
        platform: Platform | None = None,
        errors: list[str] | None = None,
        screenshots: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdapterResult:
        return cls(
            status=AdapterResultStatus.FAILED,
            phase=phase,
            message=message,
            application_id=application_id,
            platform=platform,
            errors=errors or ([message] if message else []),
            screenshots=screenshots or [],
            metadata=metadata or {},
        )


# ---------------------------------------------------------------------------
# Intervention (Phase 1)
# ---------------------------------------------------------------------------


class Intervention(BaseModel):
    """A user-facing task asking for approval or manual input."""

    intervention_id: str = Field(..., description="Stable ID.")
    application_id: str
    status: InterventionStatus
    kind: InterventionKind
    question: str
    options: list[str] = Field(default_factory=list[str])
    suggested_answer: str | None = Field(default=None)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    field_selector: str | None = Field(default=None)
    page_url: str | None = Field(default=None)
    screenshot: str | None = Field(default=None)
    created_at: datetime
    resolved_at: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
# AnswerMemory (Phase 1)
# ---------------------------------------------------------------------------

# Allowed answer-memory sources per DATA_CONTRACTS.md.
ANSWER_MEMORY_SOURCES: frozenset[str] = frozenset(
    {
        "user_confirmed",
        "profile_derived",
        "adapter_default",
    }
)


class AnswerMemory(BaseModel):
    """A user-confirmed answer keyed by normalized question pattern.

    Rules from DATA_CONTRACTS.md:
    - Do not store answers from AI unless user approved them.
    - Do not apply answer memory to semantically different questions.
    - User must be able to edit or delete memory entries.
    """

    normalized_question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    source: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    last_used: datetime | None = Field(default=None)
    use_count: int = Field(default=0, ge=0)

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in ANSWER_MEMORY_SOURCES:
            raise ValueError(f"source must be one of {ANSWER_MEMORY_SOURCES}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Artifact (Phase 1)
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """An evidence file (screenshot, trace, document) attached to an attempt."""

    attempt_id: str
    kind: str = Field(..., description="e.g. 'screenshot', 'trace', 'cv_pdf'.")
    path: str = Field(..., description="Absolute path to the artifact file.")
    created_at: datetime


# ---------------------------------------------------------------------------
# Clickable and PageObservation (Phase 3)
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    """Bounding box of a clickable element on the page."""

    x: float
    y: float
    width: float
    height: float


class Clickable(BaseModel):
    """A clickable element extracted from the DOM/accessibility tree.

    See ``DATA_CONTRACTS.md`` -> ``Clickable``.
    """

    selector: str
    tag: str = Field(..., description="HTML tag name, e.g. 'button', 'a'.")
    text: str = Field(default="")
    aria_label: str = Field(default="")
    href: str = Field(default="")
    role: str = Field(default="")
    enabled: bool = True
    visible: bool = True
    bbox: BoundingBox | None = None
    classification: ClickableClassification = ClickableClassification.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FormInfo(BaseModel):
    """Minimal info about a <form> element on the page."""

    selector: str
    action: str = Field(default="")
    method: str = Field(default="")


class InputInfo(BaseModel):
    """A visible <input> element on the page."""

    selector: str
    name: str = Field(default="")
    input_type: str = Field(default="text")
    label: str = Field(default="")
    required: bool = False
    placeholder: str = Field(default="")


class FileInputInfo(BaseModel):
    """A visible <input type='file'> element on the page."""

    selector: str
    name: str = Field(default="")
    accept: str = Field(default="")
    multiple: bool = False


class PageObservation(BaseModel):
    """Snapshot of a page's state, generated by :class:`PageObserver`.

    See ``DATA_CONTRACTS.md`` -> ``PageObservation``.
    """

    url: str
    title: str = Field(default="")
    page_state: PageState = PageState.UNKNOWN
    inputs: list[InputInfo] = Field(default_factory=list[InputInfo])
    clickables: list[Clickable] = Field(default_factory=list[Clickable])
    forms: list[FormInfo] = Field(default_factory=list[FormInfo])
    file_inputs: list[FileInputInfo] = Field(default_factory=list[FileInputInfo])
    warnings: list[str] = Field(default_factory=list[str])
    screenshot: str | None = Field(default=None, description="Path to screenshot file.")


# ---------------------------------------------------------------------------
# FormField, FieldMapping, CandidateProfile (Phase 4)
# ---------------------------------------------------------------------------

# Supported field types per DATA_CONTRACTS.md.
FORM_FIELD_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "email",
        "phone",
        "textarea",
        "select",
        "radio",
        "checkbox",
        "file",
        "date",
        "number",
        "unknown",
    }
)

# Allowed mapping sources per DATA_CONTRACTS.md.
FIELD_MAPPING_SOURCES: frozenset[str] = frozenset(
    {
        "candidate_profile",
        "application_job",
        "document_path",
        "answer_memory",
        "adapter_default",
        "ai_suggestion",
        "user_input",
        "unknown",
    }
)


class FieldOption(BaseModel):
    """An option for a select, radio, or checkbox field."""

    value: str
    label: str = Field(default="")
    selected: bool = False


class FormField(BaseModel):
    """A form field extracted from the page.

    See ``DATA_CONTRACTS.md`` -> ``FormField``.
    """

    selector: str
    name: str = Field(default="")
    label: str = Field(default="")
    type: str = Field(default="unknown")
    required: bool = False
    options: list[FieldOption] = Field(default_factory=list[FieldOption])
    current_value: str = Field(default="")
    nearby_text: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in FORM_FIELD_TYPES:
            return "unknown"
        return v


class FieldMapping(BaseModel):
    """The result of mapping a form field to candidate/job data.

    See ``DATA_CONTRACTS.md`` -> ``FieldMapping``.
    """

    field_selector: str
    value: str
    source: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_user_confirmation: bool = False
    explanation: str = Field(default="")

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in FIELD_MAPPING_SOURCES:
            return "unknown"
        return v


class CandidateProfile(BaseModel):
    """Candidate data used for deterministic field mapping.

    This is a minimal profile for Phase 4. It covers the fields referenced
    in ROADMAP.md WP 4.2 examples. Future phases may extend it.
    """

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    city: str | None = None
    country: str | None = None
    requires_sponsorship: bool | None = None
    work_authorization: str | None = None
    years_of_experience: int | None = None
    current_position: str | None = None
    website: str | None = None
    github_url: str | None = None


class FillResult(BaseModel):
    """The outcome of filling a single field."""

    field_selector: str
    status: str = Field(..., description="filled, skipped, blocked, intervention_needed")
    value: str | None = None
    source: str | None = None
    explanation: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FormFillSummary(BaseModel):
    """Summary of filling all fields on a form."""

    total_fields: int = 0
    filled: int = 0
    skipped: int = 0
    blocked: int = 0
    intervention_needed: int = 0
    results: list[FillResult] = Field(default_factory=list[FillResult])

    @property
    def all_required_fields_resolved(self) -> bool:
        """True if no required fields need intervention."""
        return self.intervention_needed == 0


__all__ = [
    # Health
    "ComponentHealth",
    "HealthReport",
    # Phase 1 contracts
    "ApplicationJob",
    "ApplicationJobDocuments",
    "ApplicationAttempt",
    "PhaseResult",
    "AdapterResult",
    "Intervention",
    "AnswerMemory",
    "ANSWER_MEMORY_SOURCES",
    "Artifact",
    # Phase 3 contracts
    "BoundingBox",
    "Clickable",
    "FormInfo",
    "InputInfo",
    "FileInputInfo",
    "PageObservation",
    # Phase 4 contracts
    "FORM_FIELD_TYPES",
    "FIELD_MAPPING_SOURCES",
    "FieldOption",
    "FormField",
    "FieldMapping",
    "CandidateProfile",
    "FillResult",
    "FormFillSummary",
]
