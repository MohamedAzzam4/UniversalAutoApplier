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
from collections.abc import Set
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
    required: bool = False
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

    Two hashes are computed:

    - :attr:`form_fingerprint` — represents the canonical form STRUCTURE
      (field tokens, types, labels, document kinds, submit control). Does
      NOT include field values. A change here means the form itself changed
      (a field was added/removed/renamed, the submit control moved, etc.).
    - :attr:`snapshot_hash` — represents the complete form STATE (structure
      + values + documents + URL + pending interventions). A change here
      means ANY part of the form state changed (a value was edited, a
      document was replaced, the URL changed, etc.).

    Approval is tied to ``snapshot_hash``. The coordinator checks both:
    a form-fingerprint mismatch returns ``approval_stale`` (form structure
    changed); a snapshot-hash mismatch also returns ``approval_stale``
    (values/documents/URL changed).
    """

    application_id: str
    application_url: str
    fields: list[SubmissionSnapshotField] = Field(default_factory=list[SubmissionSnapshotField])
    documents: list[SubmissionSnapshotDocument] = Field(
        default_factory=list[SubmissionSnapshotDocument]
    )
    pending_intervention_count: int = 0
    submit_control: SubmissionSnapshotSubmitControl | None = None
    # Explicit gate flags computed from the field list. These are NOT
    # inferred from pending_intervention_count — they are direct checks
    # on the field records.
    unresolved_required_field_count: int = 0
    high_risk_unconfirmed_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    form_fingerprint: str = ""
    snapshot_hash: str = ""

    def compute_form_fingerprint(self) -> str:
        """Compute the form STRUCTURE fingerprint.

        Includes: field tokens, types, labels, document kinds, submit
        control identity. Does NOT include field values, document content
        hashes, URL, or pending interventions. A change here means the
        form's structure changed.
        """
        structure_fields = sorted(
            [
                {
                    "token": f.field_token,
                    "type": f.field_type,
                    "label": f.label,
                }
                for f in self.fields
            ],
            key=lambda f: f.get("token", ""),
        )
        doc_kinds = sorted([d.document_kind for d in self.documents])
        canonical: dict[str, Any] = {
            "fields": structure_fields,
            "doc_kinds": doc_kinds,
            "submit_control": (
                {
                    "text": self.submit_control.text,
                    "selector": self.submit_control.selector,
                    "frame_url": self.submit_control.frame_url,
                }
                if self.submit_control
                else None
            ),
        }
        payload = json.dumps(canonical, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def compute_hash(self) -> str:
        """Compute the complete form STATE snapshot hash.

        Includes everything in the form fingerprint PLUS field values,
        document content hashes, URL, pending interventions, and the
        explicit gate flags. A change here means ANY part of the form
        state changed.
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
            "unresolved_required_field_count": self.unresolved_required_field_count,
            "high_risk_unconfirmed_count": self.high_risk_unconfirmed_count,
            "submit_control": self.submit_control.model_dump() if self.submit_control else None,
        }
        payload = json.dumps(canonical, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def with_hashes(self) -> SubmissionSnapshot:
        """Return a copy with both ``form_fingerprint`` and ``snapshot_hash`` populated."""
        return self.model_copy(
            update={
                "form_fingerprint": self.compute_form_fingerprint(),
                "snapshot_hash": self.compute_hash(),
            }
        )

    def with_hash(self) -> SubmissionSnapshot:
        """Backward-compatible alias for :meth:`with_hashes`."""
        return self.with_hashes()


# ---------------------------------------------------------------------------
# Canonical safety-state derivation
# ---------------------------------------------------------------------------

_UNRESOLVED_STATUSES = frozenset(
    {
        "intervention_needed",
        "validation_error",
        "failed",
        "blocked",
        "unfilled",
        "unsupported",
    }
)


def _count_unresolved_fields(
    fields: list[SubmissionSnapshotField],
) -> tuple[int, int]:
    """Count unresolved required fields and any unresolved fields.

    Returns ``(unresolved_required_count, any_unresolved_count)``.
    """
    unresolved_required = sum(1 for f in fields if f.status in _UNRESOLVED_STATUSES and f.required)
    unresolved_any = sum(1 for f in fields if f.status in _UNRESOLVED_STATUSES)
    return unresolved_required, unresolved_any


def derive_unresolved_required_count(fields: list[SubmissionSnapshotField]) -> int:
    """Derive the count of unresolved required fields from field data.

    Uses the same conservative logic as :func:`build_snapshot_from_report`:
    any unresolved field (even non-required) counts, for maximum safety.
    """
    unresolved_required, unresolved_any = _count_unresolved_fields(fields)
    return max(unresolved_required, unresolved_any)


def derive_unconfirmed_high_risk_count(
    fields: list[SubmissionSnapshotField],
    confirmed_tokens: Set[str] = frozenset(),
) -> int:
    """Derive the count of unconfirmed high-risk fields from field data."""
    return sum(
        1
        for f in fields
        if (f.requires_confirmation or f.risk_level.lower() == "high")
        and f.field_token not in confirmed_tokens
    )


def derive_is_complete(fields: list[SubmissionSnapshotField]) -> bool:
    """Derive completeness from field data."""
    return derive_unresolved_required_count(fields) == 0


def check_snapshot_consistency(
    snapshot: SubmissionSnapshot,
    confirmed_tokens: Set[str] = frozenset(),
) -> str:
    """Check if the snapshot's persisted aggregates match derived values.

    Returns an empty string if consistent, or a blocking reason if not.
    This is a safety net for stale/corrupted data.

    The ``high_risk_unconfirmed_count`` aggregate is compared against a
    derivation with **no** field confirmations, because the aggregate was
    computed at snapshot-creation time when no confirmations existed.
    """
    derived_unresolved = derive_unresolved_required_count(snapshot.fields)
    if derived_unresolved != snapshot.unresolved_required_field_count:
        return (
            f"Snapshot inconsistency: persisted unresolved_required_field_count="
            f"{snapshot.unresolved_required_field_count} "
            f"but field data shows {derived_unresolved}"
        )
    # Compare against the no-confirmation baseline (snapshot creation time).
    derived_high_risk = derive_unconfirmed_high_risk_count(snapshot.fields, frozenset())
    if derived_high_risk != snapshot.high_risk_unconfirmed_count:
        return (
            f"Snapshot inconsistency: persisted high_risk_unconfirmed_count="
            f"{snapshot.high_risk_unconfirmed_count} "
            f"but field data shows {derived_high_risk}"
        )
    return ""


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

    Also computes the explicit gate flags:
    - ``unresolved_required_field_count``: direct count of fields with
      status in (intervention_needed, failed, blocked) that are required.
      This is NOT inferred from pending_intervention_count — it is a
      direct check on the field records.
    - ``high_risk_unconfirmed_count``: direct count of fields with
      ``requires_confirmation=True`` or ``risk_level="high"``.
    """
    snap_fields = [
        SubmissionSnapshotField(
            field_token=f.field_token,
            label=f.label,
            field_type=f.field_type,
            filled_value=f.filled_value,
            selected_value=f.selected_value,
            status=f.status,
            required=False,  # LiveFieldRecord does not track required-ness
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

    # Compute explicit gate flags directly from field records.
    unresolved_required, unresolved_any = _count_unresolved_fields(snap_fields)
    high_risk_unconfirmed = sum(
        1 for f in snap_fields if f.requires_confirmation or f.risk_level.lower() == "high"
    )

    snap = SubmissionSnapshot(
        application_id=application_id,
        application_url=application_url,
        fields=snap_fields,
        documents=snap_docs,
        pending_intervention_count=pending_intervention_count,
        submit_control=submit_control,
        unresolved_required_field_count=max(unresolved_required, unresolved_any),
        high_risk_unconfirmed_count=high_risk_unconfirmed,
    )
    return snap.with_hashes()


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
    "check_snapshot_consistency",
    "derive_is_complete",
    "derive_unconfirmed_high_risk_count",
    "derive_unresolved_required_count",
]
