"""WQ-8 single-use real-submission authorization.

This module defines the tighter, single-use authorization that overshadows
the existing controlled-submission approval/claim/result stack. It is
ADDITIVE: when an active authorization exists for an application, the
:class:`SubmissionCoordinator` enforces it before a submit click; when none
exists, the coordinator behaves byte-for-byte as before.

Safety invariants (WQ-8 contract):

- **Exactly one** real application submission total. Enforced by the
  :data:`MAX_TOTAL_REAL_SUBMISSIONS` constant, by creation-time refusal
  (never create a second authorization after a converted submission or an
  active authorization for another application), and by the coordinator gate.
- The authorization is bound to ALL of: ``application_id``, job identity
  (company + title), target ATS URL, the frozen ``review_plan_hash``,
  CV/document SHA-256 content hashes, an expiry, and one-time (consumed)
  state. Any binding change invalidates the authorization.
- It is consumed (compare-and-set) as part of submission initiation, before
  the browser starts, so it can never be reused.
- Default remains submission forbidden: creating an authorization is an
  explicit, owner-only Phase B step.

The ``review_plan_hash`` is the frozen identifier that appears in the Phase A
owner review packet and in the Phase B owner approval command. It
deterministically covers the entire final plan (application identity, job
identity, target URL, planned answers/sources/options/risk, document
hashes, submit-control identity, intervention summary). ``generated_at`` is
excluded so identical plans hash identically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Absolute limit
# ---------------------------------------------------------------------------

# The absolute maximum number of real application submissions for the whole
# WQ-8 workpackage. Hard-coded, not configurable — loosening it requires a
# code review, not an environment variable.
MAX_TOTAL_REAL_SUBMISSIONS = 1

# Result states that count as "a real submission was attempted (clicked)".
# These delimit the absolute limit during creation-time checks.
# Public name: imported by the authorization store (see
# ``authorization_store.py``) and by tests.
CLICKED_ATTEMPT_STATES = frozenset(
    {
        "submitted_confirmed",
        "outcome_unknown",
        "validation_failed",
        "blocked_user_action",
    }
)

# Backward-compatible private alias kept for the module's own history. The
# store uses the public :data:`CLICKED_ATTEMPT_STATES` name.
_CLICKED_ATTEMPT_STATES = CLICKED_ATTEMPT_STATES


# ---------------------------------------------------------------------------
# Review plan hash
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a dict view of a field/document entry (model or dict)."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    serializer = getattr(value, "model_dump", None)
    if callable(serializer):
        raw = serializer()
        if isinstance(raw, dict):
            return cast("dict[str, Any]", raw)
    return {}


def _file_content_hash(path: str | None) -> str:
    """Return the SHA-256 (first 32 hex) of a file's bytes, or ''."""
    if not path:
        return ""
    import os

    try:
        if not os.path.isfile(path):
            return ""
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:32]
    except OSError:
        return ""


def compute_review_plan_hash(plan: dict[str, Any]) -> str:
    """Deterministic SHA-256 of a canonical review plan.

    The plan is canonicalized with ``sort_keys=True`` and ``default=str`` so
    insertion/property order never changes the hash. ``generated_at`` is
    stripped before hashing (identical plans hash identically).
    """
    canonical: dict[str, Any] = dict(plan)
    canonical.pop("generated_at", None)
    payload = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_review_plan(
    *,
    application_id: str,
    company: str,
    job_title: str,
    application_url: str,
    fields: list[Any],
    documents: list[Any],
    submit_control_text: str = "",
    submit_control_selector: str = "",
    submit_control_frame_url: str = "",
    pending_intervention_count: int = 0,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical review plan from persisted/form state.

    ``fields`` and ``documents`` may be :class:`SubmissionSnapshotField`
    / :class:`SubmissionSnapshotDocument` Pydantic models or plain dicts.
    Field entries contribute: ``field_token``, ``label``, ``field_type``,
    ``filled_value``, ``selected_value``, ``status``, ``risk_level``,
    ``requires_confirmation``. Document entries contribute ``document_kind``
    and ``content_hash`` (their ``path`` is reduced to the filename only so
    the hash is machine-independent).
    """
    field_rows: list[dict[str, Any]] = []
    for f in fields or []:
        data = _as_dict(f)
        field_rows.append(
            {
                "field_token": data.get("field_token", ""),
                "label": data.get("label", ""),
                "field_type": data.get("field_type", ""),
                "filled_value": data.get("filled_value", ""),
                "selected_value": data.get("selected_value", ""),
                "status": data.get("status", ""),
                "risk_level": data.get("risk_level", ""),
                "requires_confirmation": bool(data.get("requires_confirmation", False)),
            }
        )
    field_rows.sort(key=lambda r: r.get("field_token", ""))

    doc_rows: list[dict[str, str]] = []
    for d in documents or []:
        data = _as_dict(d)
        path = str(data.get("path", ""))
        doc_rows.append(
            {
                "document_kind": data.get("document_kind", ""),
                "content_hash": data.get("content_hash", ""),
                "filename": path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] if path else "",
            }
        )
    doc_rows.sort(key=lambda r: (r.get("document_kind", ""), r.get("filename", "")))

    submit_control: dict[str, str] | None = None
    if submit_control_text or submit_control_selector:
        submit_control = {
            "text": submit_control_text,
            "selector": submit_control_selector,
            "frame_url": submit_control_frame_url,
        }

    return {
        "application_id": application_id,
        "company": company,
        "job_title": job_title,
        "application_url": application_url,
        "fields": field_rows,
        "documents": doc_rows,
        "submit_control": submit_control,
        "pending_intervention_count": int(pending_intervention_count),
        "generated_at": (generated_at or _utcnow()).isoformat(),
    }


def compute_frozen_review_plan_hash(
    *,
    application_id: str,
    company: str,
    job_title: str,
    application_url: str,
    fields: list[Any],
    documents: list[Any],
    submit_control_text: str = "",
    submit_control_selector: str = "",
    submit_control_frame_url: str = "",
    pending_intervention_count: int = 0,
    generated_at: datetime | None = None,
) -> str:
    """Compute the frozen ``review_plan_hash`` from the current state."""
    plan = build_review_plan(
        application_id=application_id,
        company=company,
        job_title=job_title,
        application_url=application_url,
        fields=fields,
        documents=documents,
        submit_control_text=submit_control_text,
        submit_control_selector=submit_control_selector,
        submit_control_frame_url=submit_control_frame_url,
        pending_intervention_count=pending_intervention_count,
        generated_at=generated_at,
    )
    return compute_review_plan_hash(plan)


# ---------------------------------------------------------------------------
# Authorization model
# ---------------------------------------------------------------------------


class SubmissionAuthorization(BaseModel):
    """A single-use, expiry-bound real-submission authorization (WQ-8).

    Bound to ``application_id`` + job identity + ``application_url`` +
    ``review_plan_hash`` + ``document_hashes``. ``authorization_id`` is
    deterministic from (application_id, review_plan_hash) so re-approving the
    identical plan is idempotent.
    """

    authorization_id: str
    application_id: str
    application_url: str
    job_company: str = ""
    job_title: str = ""
    review_plan_hash: str = ""
    document_hashes: list[str] = Field(default_factory=list[str])
    created_at: datetime = Field(default_factory=_utcnow)
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= _utcnow()

    @property
    def is_active(self) -> bool:
        """True when neither consumed, revoked, nor expired."""
        return self.consumed_at is None and self.revoked_at is None and not self.is_expired


def make_authorization_id(application_id: str, review_plan_hash: str) -> str:
    """Deterministic authorization ID from app + frozen plan hash."""
    source = ":".join((application_id, review_plan_hash, "wq8-auth"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "MAX_TOTAL_REAL_SUBMISSIONS",
    "SubmissionAuthorization",
    "build_review_plan",
    "compute_review_plan_hash",
    "compute_frozen_review_plan_hash",
    "make_authorization_id",
    "_file_content_hash",
]
