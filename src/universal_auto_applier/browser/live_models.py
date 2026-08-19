"""Structured results produced by the live browser dry-run.

These models deliberately contain no Playwright objects. They are safe to
serialize as evidence and keep the browser dependency outside ``core``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LiveRunStatus = Literal["review_ready", "needs_user_input", "failed", "recon_complete"]


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
    """Evidence that a document upload was attempted."""

    page_url: str
    selector: str
    document_kind: Literal["cv", "cover_letter", "attachment", "unknown"]
    path: str
    status: Literal["uploaded", "missing", "failed"]
    message: str = ""


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


__all__ = [
    "LiveClickRecord",
    "LiveFieldRecord",
    "LiveFormObservation",
    "LiveRunReport",
    "LiveRunStatus",
    "LiveUploadRecord",
    "SubmitInterlockCounters",
]
