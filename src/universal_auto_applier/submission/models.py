"""Controlled final submission — snapshot, approval, and result contracts.

This module defines the data structures for the controlled-final-submission
workpackage. These are pure data models (no Playwright, no DB) that flow
through the :class:`SubmissionCoordinator` and the submission API/CLI.

Safety invariants:
- A :class:`SubmissionSnapshot` is a deterministic fingerprint of the
  form state at approval time. Any change to fields, documents, URL, or
  submit control invalidates the snapshot hash.
- A :class:`SubmissionApproval` is tied to a specific snapshot hash, NOT
  only to the application ID. It is one-time: it cannot be reused for a
  different snapshot or a different application.
- A :class:`SubmissionClaim` is a one-time transactional lock that
  prevents duplicate clicks across process restarts, dashboard refreshes,
  concurrent requests, and double-clicks.
- :class:`SubmissionResult` distinguishes confirmed submission from
  ambiguous outcome. Ambiguous outcomes block automatic retry.

See ``docs/generalization/DRY_RUN_LEVELS.md`` Level 3 and
``docs/testing/CONTROLLED_REAL_SUBMISSION_TEST_PLAN.md``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from universal_auto_applier.browser.live_models import (
    LiveFieldRecord,
    LiveUploadRecord,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Submission snapshot
# ---------------------------------------------------------------------------


class SubmissionSnapshotField(BaseModel):
    """One field's final state in the submission snapshot."""

    field_token: str
    label: str = ""
    field_type: str
    filled_value: str = ""
    selected_value: str = ""
    status: str
    requires_confirmation: bool = False
    risk_level: str = ""


class SubmissionSnapshotDocument(BaseModel):
    """One uploaded document in the submission snapshot."""

    document_kind: str
    path: str
    content_hash: str = ""


class SubmissionSnapshotSubmitControl(BaseModel):
    """The final submit control detected on the page."""

    text: str = ""
    selector: str = ""
    frame_url: str = ""
    classification: str = "dangerous_submit"


class SubmissionSnapshot(BaseModel):
    """A deterministic fingerprint of the form state at approval time.

    The :attr:`snapshot_hash` is computed from all fields below. Any
    change to a field value, document, URL, form structure, intervention
    count, or submit control produces a different hash, invalidating any
    prior approval.
    """

    application_id: str
    application_url: str
    fields: list[SubmissionSnapshotField] = Field(default_factory=list[SubmissionSnapshotField])
    documents: list[SubmissionSnapshotDocument] = Field(
        default_factory=list[SubmissionSnapshotDocument]
    )
    pending_intervention_count: int = 0
    submit_control: SubmissionSnapshotSubmitControl | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    snapshot_hash: str = ""

    def compute_hash(self) -> str:
        """Compute the deterministic snapshot hash.

        The hash is SHA-256 of a canonical JSON representation of the
        snapshot (excluding ``created_at`` and the hash itself). The
        canonical representation sorts list items by stable keys so the
        same logical form state always produces the same hash.
        """
        canonical: dict[str, Any] = {
            "application_id": self.application_id,
            "application_url": self.application_url,
            "fields": sorted(
                [f.model_dump() for f in self.fields],
                key=lambda f: f.get("field_token", ""),
            ),
            "documents": sorted(
                [d.model_dump() for d in self.documents],
                key=lambda d: (d.get("document_kind", ""), d.get("path", "")),
            ),
            "pending_intervention_count": self.pending_intervention_count,
            "submit_control": self.submit_control.model_dump() if self.submit_control else None,
        }
        payload = json.dumps(canonical, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def with_hash(self) -> SubmissionSnapshot:
        """Return a copy with ``snapshot_hash`` populated."""
        return self.model_copy(update={"snapshot_hash": self.compute_hash()})


def build_snapshot_from_report(
    *,
    application_id: str,
    application_url: str,
    fields: list[LiveFieldRecord],
    uploads: list[LiveUploadRecord],
    pending_intervention_count: int,
    submit_control_text: str = "",
    submit_control_selector: str = "",
    submit_control_frame_url: str = "",
) -> SubmissionSnapshot:
    """Build a :class:`SubmissionSnapshot` from a live run report.

    Computes content hashes for uploaded documents by reading the files
    from disk. If a file cannot be read, the content hash is empty
    (which still contributes to the snapshot hash, so a missing file
    invalidates the approval).
    """
    snap_fields = [
        SubmissionSnapshotField(
            field_token=f.field_token,
            label=f.label,
            field_type=f.field_type,
            filled_value=f.filled_value,
            selected_value=f.selected_value,
            status=f.status,
            requires_confirmation=f.requires_confirmation,
            risk_level=f.risk_level,
        )
        for f in fields
    ]

    snap_docs: list[SubmissionSnapshotDocument] = []
    for u in uploads:
        content_hash = ""
        try:
            path = Path(u.path)
            if path.exists() and path.is_file():
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:32]
        except OSError:
            content_hash = ""
        snap_docs.append(
            SubmissionSnapshotDocument(
                document_kind=u.document_kind,
                path=u.path,
                content_hash=content_hash,
            )
        )

    submit_control: SubmissionSnapshotSubmitControl | None = None
    if submit_control_text or submit_control_selector:
        submit_control = SubmissionSnapshotSubmitControl(
            text=submit_control_text,
            selector=submit_control_selector,
            frame_url=submit_control_frame_url,
        )

    snap = SubmissionSnapshot(
        application_id=application_id,
        application_url=application_url,
        fields=snap_fields,
        documents=snap_docs,
        pending_intervention_count=pending_intervention_count,
        submit_control=submit_control,
    )
    return snap.with_hash()


# ---------------------------------------------------------------------------
# Submission approval
# ---------------------------------------------------------------------------


class SubmissionApproval(BaseModel):
    """A one-time approval for a specific snapshot.

    The approval is tied to ``snapshot_hash`` AND ``application_id``.
    Changing the form state produces a new snapshot hash, which will not
    match this approval's ``snapshot_hash`` — the approval is stale.

    The approval is consumed by :class:`SubmissionClaim` when a submit
    click is attempted. Once consumed, the approval cannot be reused.
    """

    approval_id: str
    application_id: str
    snapshot_hash: str
    created_at: datetime = Field(default_factory=_utcnow)
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """True if the approval is neither consumed nor revoked."""
        return self.consumed_at is None and self.revoked_at is None


# ---------------------------------------------------------------------------
# Submission result state machine
# ---------------------------------------------------------------------------


class SubmissionResultState(StrEnum):
    """Terminal states for a submission attempt.

    - ``submitted_confirmed``: strong confirmation (ATS confirmation page,
      application reference, recognized success state). The only state
      that transitions the application to ``APPLIED``.
    - ``validation_failed``: client-side or server-side validation errors
      appeared after the click. The application returns to review or
      intervention. No automatic resubmit.
    - ``blocked_user_action``: CAPTCHA, login, MFA, signature, legal
      declaration, or payment required. Stops for user intervention.
    - ``approval_stale``: the snapshot changed after approval. The
      approval is invalidated; the application returns to review.
    - ``submission_not_allowed``: one or more safety gates failed (feature
      disabled, no approval, pending interventions, etc.). No click.
    - ``submit_control_ambiguous``: more than one final-submit control
      detected, or the control is invisible/disabled. No click.
    - ``outcome_unknown``: the click happened but no strong confirmation
      was detected within the bounded wait period. Blocks automatic retry.
      Requires explicit human review (``NEEDS_REVIEW`` status).
    - ``already_submitted``: the application was already submitted in a
      previous attempt. No second click.
    """

    SUBMITTED_CONFIRMED = "submitted_confirmed"
    VALIDATION_FAILED = "validation_failed"
    BLOCKED_USER_ACTION = "blocked_user_action"
    APPROVAL_STALE = "approval_stale"
    SUBMISSION_NOT_ALLOWED = "submission_not_allowed"
    SUBMIT_CONTROL_AMBIGUOUS = "submit_control_ambiguous"
    OUTCOME_UNKNOWN = "outcome_unknown"
    ALREADY_SUBMITTED = "already_submitted"


class SubmissionResult(BaseModel):
    """The outcome of one submission attempt."""

    application_id: str
    approval_id: str
    snapshot_hash_at_submit: str
    state: SubmissionResultState
    clicked: bool = False
    pre_submit_screenshot: str | None = None
    post_submit_screenshot: str | None = None
    post_submit_url: str = ""
    post_submit_dom_path: str | None = None
    confirmation_evidence: str = ""
    validation_errors: list[str] = Field(default_factory=list[str])
    error_message: str = ""
    attempted_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_terminal_success(self) -> bool:
        """True only for confirmed submission."""
        return self.state == SubmissionResultState.SUBMITTED_CONFIRMED


# ---------------------------------------------------------------------------
# Submission claim (one-time transactional lock)
# ---------------------------------------------------------------------------


class SubmissionClaim(BaseModel):
    """A transactional one-time claim that prevents duplicate clicks.

    The claim is acquired BEFORE the submit click and released (consumed)
    AFTER the outcome is recorded. If the process crashes between
    acquisition and consumption, the claim remains held and blocks
    automatic retry — the user must explicitly review and release it.
    """

    claim_id: str
    application_id: str
    approval_id: str
    snapshot_hash: str
    acquired_at: datetime = Field(default_factory=_utcnow)
    consumed_at: datetime | None = None
    consumed_state: str = ""  # SubmissionResultState value


__all__ = [
    "SubmissionApproval",
    "SubmissionClaim",
    "SubmissionResult",
    "SubmissionResultState",
    "SubmissionSnapshot",
    "SubmissionSnapshotDocument",
    "SubmissionSnapshotField",
    "SubmissionSnapshotSubmitControl",
    "build_snapshot_from_report",
]
