"""Structured results produced by the live browser dry-run.

These models deliberately contain no Playwright objects. They are safe to
serialize as evidence and keep the browser dependency outside ``core``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LiveRunStatus = Literal["review_ready", "needs_user_input", "failed"]


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


class LiveUploadRecord(BaseModel):
    """Evidence that a document upload was attempted."""

    page_url: str
    selector: str
    document_kind: Literal["cv", "cover_letter", "attachment", "unknown"]
    path: str
    status: Literal["uploaded", "missing", "failed"]
    message: str = ""


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


__all__ = [
    "LiveClickRecord",
    "LiveFieldRecord",
    "LiveRunReport",
    "LiveRunStatus",
    "LiveUploadRecord",
]
