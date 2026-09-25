"""Structured results produced by the live browser dry-run.

These models deliberately contain no Playwright objects. They are safe to
serialize as evidence and keep the browser dependency outside ``core``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

LiveRunStatus = Literal["review_ready", "needs_user_input", "failed", "recon_complete"]
LiveUploadStatus = Literal[
    "selection_verified",
    "remote_accepted",
    "rejected",
    "unknown",
    "missing",
    "failed",
]
LiveUploadEvidenceSource = Literal[
    "native_selection",
    "declared_site_status",
    "input_constraint",
    "unknown",
]
LiveUploadContract = Literal["native_final_submit", "declared_async_status"]


class LiveClickRecord(BaseModel):
    """One navigation action performed by the live browser runner."""

    step_number: int = Field(..., ge=1)
    from_url: str
    to_url: str = ""
    text: str = ""
    classification: str
    selector: str
    frame_url: str = ""
    screenshot: str | None = None


class LiveFieldRecord(BaseModel):
    """Outcome of mapping and executing one form-field action."""

    page_url: str
    selector: str
    label: str = ""
    field_type: str
    status: Literal["filled", "skipped", "blocked", "intervention_needed", "failed"]
    source: str | None = None
    explanation: str = ""
    field_token: str = ""
    proposed_answer: str | None = None
    confidence: float | None = None
    evidence_summary: str = ""
    category: str = ""
    risk_level: str = ""
    requires_confirmation: bool = False
    # Requiredness as observed from the live form control. Older reports and
    # synthetic callers retain a safe backward-compatible default.
    required: bool = False
    # For radio/select/checkbox: available option labels.
    options: list[str] = Field(default_factory=list[str])
    # For radio/checkbox/select: value currently selected in the DOM, recorded
    # separately from ``proposed_answer`` so callers can distinguish "what the
    # form already had" from "what the runner filled".
    selected_value: str = ""
    # The value actually written by the runner (deterministic or LLM). Empty
    # when no fill was attempted (skipped/blocked/intervention_needed).
    filled_value: str = ""


class LiveUploadRecord(BaseModel):
    """Evidence about native file selection and, when observable, site acceptance."""

    page_url: str
    selector: str
    document_kind: Literal["cv", "cover_letter", "transcript", "attachment", "unknown"]
    path: str
    status: LiveUploadStatus
    selected_file_names: list[str] = Field(default_factory=list[str])
    observed_constraints: dict[str, str | bool] = Field(default_factory=dict[str, str | bool])
    evidence_source: LiveUploadEvidenceSource = "unknown"
    # `None` preserves selected-file evidence without qualifying it for final
    # review readiness. A flow must explicitly declare how the file is sent.
    upload_contract: LiveUploadContract | None = None
    evidence_detail: str = ""
    message: str = ""

    @model_validator(mode="after")
    def validate_upload_evidence(self) -> LiveUploadRecord:
        """Do not allow a successful status without matching evidence."""
        expected_name = self.path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        observed_names = {name.casefold() for name in self.selected_file_names}
        matched_selection = bool(expected_name and expected_name in observed_names)
        if self.status == "selection_verified":
            if (
                self.evidence_source != "native_selection"
                or not matched_selection
                or not self.evidence_detail.strip()
                or self.upload_contract not in {None, "native_final_submit"}
            ):
                raise ValueError(
                    "selection_verified requires matching native selected-filename evidence"
                )
        elif self.status in {"remote_accepted", "rejected"}:
            if (
                self.evidence_source != "declared_site_status"
                or not matched_selection
                or not self.evidence_detail.strip()
                or self.upload_contract != "declared_async_status"
            ):
                raise ValueError(
                    f"{self.status} requires matching selected filenames and declared site-status evidence"
                )
        return self


class LiveFormObservation(BaseModel):
    """Structural observation of an application form reached in recon mode.

    WQ-7B records what the form looks like WITHOUT touching it: counts of
    visible controls and file inputs, submit-handler presence, and field
    labels. No values are ever written to the page.
    """

    page_url: str
    title: str = ""
    visible_control_count: int = 0
    file_input_count: int = 0
    has_dangerous_submit: bool = False
    field_labels: list[str] = Field(default_factory=list[str])
    detected_at: datetime
    # Present when the reached form embeds an anti-bot widget (for example
    # an hCaptcha/reCAPTCHA challenge). Recon only observes the structure; it
    # never interacts with the widget. Kept as evidence, not as a bypass.
    embedded_blocker: str | None = None


class SubmitInterlockCounters(BaseModel):
    """Structured submit-interlock evidence recorded on EVERY WQ-7C run.

    Zeros are meaningful: an interlock installed and recording zeros is
    proof that no submission was attempted, independent of the runner's
    intended behavior. The fields mirror the browser-side counters in
    ``browser/submit_interlock.py`` plus the UAA-level submit-click count
    (calls into ``LiveBrowserRunner.attempt_submit``, which the dry-run
    never performs).
    """

    installed: bool = False
    # UAA-level attempts to click a final submit control. The dry-run never
    # performs one, so a truthful run records zero here.
    uaa_submit_clicks: int = 0
    # Browser-side interlock counters (see submit_interlock.py).
    submit_events: int = 0
    form_submit_calls: int = 0
    request_submit_calls: int = 0
    dispatch_submit_events: int = 0
    blocked_submissions: int = 0
    navigation_attempts: int = 0
    # Network-level suspected application-submission instrumentation.
    # The current codebase has NO such detector, so this honestly reports
    # that limitation rather than inventing an unobserved signal.
    network_submission_detector: str = "not_instrumented"


class BlockedHttpRequest(BaseModel):
    """Sanitized evidence that preparation blocked a routed HTTP request."""

    method: str
    resource_type: str = "unknown"
    destination_origin: str = "unknown-origin"
    reason: str


class LiveRunReport(BaseModel):
    """Complete machine-readable report for one live browser dry-run."""

    application_id: str
    status: LiveRunStatus = "failed"
    started_at: datetime
    finished_at: datetime | None = None
    initial_url: str
    final_url: str = ""
    stopped_reason: str = ""
    click_path: list[LiveClickRecord] = Field(default_factory=list[LiveClickRecord])
    fields: list[LiveFieldRecord] = Field(default_factory=list[LiveFieldRecord])
    uploads: list[LiveUploadRecord] = Field(default_factory=list[LiveUploadRecord])
    screenshots: list[str] = Field(default_factory=list[str])
    trace_path: str | None = None
    dom_snapshot_path: str | None = None
    report_path: str | None = None
    errors: list[str] = Field(default_factory=list[str])
    # True only when this run confirmed a submission. False means UAA did not
    # confirm one; when request_outcome_unknown is true, it does not prove the
    # remote site did not receive an application.
    submitted: bool = False
    recon_observation: LiveFormObservation | None = None
    # WQ-7C synthetic mutation evidence.
    #
    # Every mutation pass (initial extraction, then each bounded reveal pass)
    # builds and freezes its OWN pre-mutation plan. The FIRST pass is exposed
    # through the legacy ``plan_hash``/``mutation_plan_path`` for backward
    # compatibility; ``plan_chain_hash`` covers the deterministic ORDERED
    # chain, and ``plan_chain_hashes``/``mutation_plan_chain_paths`` let
    # evidence consumers re-verify every plan that actually ran.
    plan_hash: str = ""
    mutation_plan_path: str | None = None
    plan_chain_hash: str = ""
    plan_chain_hashes: list[str] = Field(default_factory=list[str])
    mutation_plan_chain_paths: list[str] = Field(default_factory=list[str])
    # Structured zero-tolerant submit evidence (WQ-7C closure item 2).
    submit_interlock: SubmitInterlockCounters | None = None
    # LiveBrowserRunner context-route coverage is limited to HTTP requests
    # observed by Playwright. It does not cover WebSocket frames or
    # server-side effects of GET requests. Request paths, queries, headers,
    # and bodies are excluded. Other browser preparation paths may not install
    # this guard and must not inherit a coverage claim from this report.
    request_interlock_installed: bool = False
    request_interlock_coverage: Literal["none", "playwright_context_http_routes"] = "none"
    request_interlock_limitations: list[str] = Field(
        default_factory=lambda: [
            "WebSocket frames and GET endpoints with side effects are outside HTTP-route coverage.",
            "Dormant service workers in caller-owned contexts cannot be enumerated.",
        ]
    )
    request_interlock_failure: str | None = None
    request_outcome_unknown: bool = False
    blocked_http_request_count: int = 0
    blocked_http_requests: list[BlockedHttpRequest] = Field(
        default_factory=list[BlockedHttpRequest]
    )


__all__ = [
    "LiveClickRecord",
    "LiveFieldRecord",
    "LiveFormObservation",
    "LiveRunReport",
    "LiveRunStatus",
    "LiveUploadRecord",
    "LiveUploadEvidenceSource",
    "LiveUploadContract",
    "LiveUploadStatus",
    "BlockedHttpRequest",
    "SubmitInterlockCounters",
]
