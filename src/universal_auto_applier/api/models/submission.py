"""Typed API response models for the controlled-submission live-review API.

These models define the complete contract between the API and the future
dashboard. They ensure the dashboard never needs to construct snapshots
from in-memory ReviewState — it receives everything from the persisted
observation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LiveReviewField(BaseModel):
    """One field in the persisted live-review snapshot."""

    field_token: str
    label: str = ""
    field_type: str
    required: bool = False
    filled_value: str = ""
    selected_value: str = ""
    status: str
    risk_level: str = ""
    requires_confirmation: bool = False
    confirmed: bool = False
    evidence: str = ""
    source: str = ""
    options: list[str] = Field(default_factory=list[str])
    validation_error: str = ""


class LiveReviewDocument(BaseModel):
    """One uploaded document in the persisted snapshot."""

    document_kind: str
    filename: str = ""
    path: str
    content_hash: str = ""
    exists: bool = True
    readable: bool = True


class LiveReviewSubmitControl(BaseModel):
    """The final submit control detected on the page."""

    text: str = ""
    selector: str = ""
    frame_url: str = ""


class LiveReviewSnapshotResponse(BaseModel):
    """Complete persisted live-review snapshot."""

    application_id: str
    external_job_id: str = ""
    company: str = ""
    job_title: str = ""
    application_url: str = ""
    platform: str = ""
    observation_timestamp: datetime | None = None
    form_fingerprint: str = ""
    snapshot_hash: str = ""
    is_complete: bool = False
    is_stale: bool = False
    submit_control: LiveReviewSubmitControl | None = None
    fields: list[LiveReviewField] = Field(default_factory=list[LiveReviewField])
    documents: list[LiveReviewDocument] = Field(default_factory=list[LiveReviewDocument])
    pending_intervention_count: int = 0
    unresolved_required_field_count: int = 0
    unconfirmed_high_risk_count: int = 0
    active_approval_id: str | None = None
    approval_state: str = "none"
    approved_snapshot_hash: str | None = None
    approval_is_stale: bool = False
    can_approve: bool = False
    approve_blocking_reason: str = ""
    can_submit: bool = False
    submit_blocking_reason: str = ""
    enable_real_submission: bool = False
    latest_submission_state: str | None = None
    latest_submission_error: str | None = None
    latest_submission_timestamp: datetime | None = None


class LiveReviewConfirmHighRiskRequest(BaseModel):
    snapshot_hash: str
    field_tokens: list[str]
    confirm: bool = False


class LiveReviewApproveRequest(BaseModel):
    snapshot_hash: str
    confirm: bool = False


class LiveReviewSubmitRequest(BaseModel):
    approval_id: str
    confirm: bool = False


class LiveReviewObserveResponse(BaseModel):
    snapshot: LiveReviewSnapshotResponse


class LiveReviewStatusResponse(BaseModel):
    snapshot: LiveReviewSnapshotResponse


class LiveReviewConfirmHighRiskResponse(BaseModel):
    snapshot: LiveReviewSnapshotResponse
    confirmed_tokens: list[str] = Field(default_factory=list[str])


class LiveReviewApproveResponse(BaseModel):
    application_id: str
    approval_id: str
    snapshot_hash: str
    approved: bool = True


class LiveReviewRevokeResponse(BaseModel):
    application_id: str
    revoked: bool = True


class LiveReviewSubmitResponse(BaseModel):
    application_id: str
    state: str
    clicked: bool
    confirmation_evidence: str = ""
    error_message: str = ""
