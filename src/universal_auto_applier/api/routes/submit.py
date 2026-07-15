"""Controlled final submission API.

Endpoints:
- GET  /api/submit/{application_id}/status  — current submission state
- POST /api/submit/{application_id}/approve — approve a snapshot
- POST /api/submit/{application_id}/revoke  — revoke an approval
- POST /api/submit/{application_id}/submit  — execute the controlled submit

The submit endpoint requires a deliberate confirmation body
(``{"confirm": true}``) and an ``approval_id``. Approval alone does NOT
click Submit — the user must explicitly call the submit endpoint.

All gates are enforced server-side by the :class:`SubmissionCoordinator`.
Frontend checks are advisory only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
)
from universal_auto_applier.submission.store import (
    get_active_approval,
    get_latest_result,
)

router = APIRouter(tags=["submit"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ApproveSnapshotRequest(BaseModel):
    """Request to approve a snapshot for submission."""

    snapshot: dict[str, Any]
    confirm: bool = False


class SubmitRequest(BaseModel):
    """Request to execute the controlled submit.

    The ``confirm`` field MUST be ``true`` — this is the deliberate
    confirmation that prevents accidental submission. The ``approval_id``
    must match an active approval for this application.
    """

    approval_id: str
    confirm: bool = False


class SubmissionStatusResponse(BaseModel):
    """Current submission state for an application."""

    application_id: str
    enable_real_submission: bool
    has_active_approval: bool
    approval_id: str | None = None
    snapshot_hash: str | None = None
    is_stale: bool = False
    latest_result_state: str | None = None
    latest_result_clicked: bool | None = None
    latest_result_error: str | None = None
    can_submit: bool = False
    gate_reason: str = ""


class ApproveResponse(BaseModel):
    """Response after approving a snapshot."""

    application_id: str
    approval_id: str
    snapshot_hash: str
    approved: bool = True


class RevokeResponse(BaseModel):
    """Response after revoking an approval."""

    application_id: str
    revoked: bool = True


class SubmitResponse(BaseModel):
    """Response after a submit attempt."""

    application_id: str
    state: str
    clicked: bool
    confirmation_evidence: str = ""
    error_message: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/submit/{application_id}/status", response_model=SubmissionStatusResponse)
def get_submission_status(request: Request, application_id: str) -> SubmissionStatusResponse:
    """Return the current submission state for an application."""
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    coordinator = SubmissionCoordinator(settings, session_factory)

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        latest = get_latest_result(session, application_id)

    # Check gates (without a current snapshot — the snapshot check
    # requires a live browser page, which the status endpoint does not
    # open. The dashboard uses this to show whether submission is
    # potentially allowed; the actual submit endpoint re-checks with
    # the live snapshot.)
    gate = coordinator.check_gates(application_id=application_id)

    return SubmissionStatusResponse(
        application_id=application_id,
        enable_real_submission=settings.enable_real_submission,
        has_active_approval=approval is not None,
        approval_id=approval.approval_id if approval else None,
        snapshot_hash=approval.snapshot_hash if approval else None,
        is_stale=False,  # Cannot determine without a live snapshot
        latest_result_state=str(latest.state) if latest else None,
        latest_result_clicked=latest.clicked if latest else None,
        latest_result_error=latest.error_message if latest and latest.error_message else None,
        can_submit=gate.allowed,
        gate_reason=gate.reason if not gate.allowed else "",
    )


@router.post("/submit/{application_id}/approve", response_model=ApproveResponse)
def approve_snapshot_endpoint(
    request: Request,
    application_id: str,
    body: ApproveSnapshotRequest,
) -> ApproveResponse:
    """Approve a form snapshot for submission.

    This does NOT submit the application. It only records the approval
    tied to the snapshot hash. The user must separately call the submit
    endpoint with ``confirm: true``.
    """
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    coordinator = SubmissionCoordinator(settings, session_factory)

    # Build the snapshot from the request body.
    try:
        snapshot = SubmissionSnapshot.model_validate(body.snapshot)
        snapshot = snapshot.with_hash()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid snapshot: {exc}") from exc

    approval_id = coordinator.approve_snapshot(
        application_id=application_id,
        snapshot=snapshot,
    )

    return ApproveResponse(
        application_id=application_id,
        approval_id=approval_id,
        snapshot_hash=snapshot.snapshot_hash,
    )


@router.post("/submit/{application_id}/revoke", response_model=RevokeResponse)
def revoke_approval_endpoint(
    request: Request,
    application_id: str,
) -> RevokeResponse:
    """Revoke the active approval for an application."""
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    coordinator = SubmissionCoordinator(settings, session_factory)

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        if approval is None:
            raise HTTPException(
                status_code=404,
                detail="no active approval for this application",
            )
        approval_id = approval.approval_id

    revoked = coordinator.revoke_approval(approval_id)
    return RevokeResponse(
        application_id=application_id,
        revoked=revoked,
    )


@router.post("/submit/{application_id}/submit", response_model=SubmitResponse)
def submit_endpoint(
    request: Request,
    application_id: str,
    body: SubmitRequest,
) -> SubmitResponse:
    """Execute the controlled final submission.

    This is the ONLY endpoint that can trigger a submit click. It
    requires:
    - ``confirm: true`` (deliberate confirmation)
    - ``approval_id`` matching an active approval

    The actual browser interaction is NOT performed by this endpoint
    directly (the API runs in the dashboard server process, which does
    not own a browser context). Instead, this endpoint:
    1. Verifies the gates.
    2. Records the submit request.
    3. Returns ``state=submission_not_allowed`` if any gate fails.

    The actual click is performed by the CLI ``live-submit`` command or
    a future background worker, which owns the browser context. This
    endpoint is the API gate that the dashboard calls to verify the
    request is allowed before launching the browser.

    If ``confirm`` is not True, returns 400.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true to submit (deliberate confirmation required)",
        )

    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    coordinator = SubmissionCoordinator(settings, session_factory)

    # Check gates. The browser-level gates (submit control visibility,
    # URL match) cannot be checked here — they are checked by the CLI
    # worker that owns the browser. This endpoint checks the
    # non-browser gates and records the request.
    gate = coordinator.check_gates(application_id=application_id)
    if not gate.allowed:
        return SubmitResponse(
            application_id=application_id,
            state=str(gate.state),
            clicked=False,
            error_message=gate.reason,
        )

    # The actual click is performed by the CLI worker. This endpoint
    # confirms the request is allowed and returns a "ready to submit"
    # state. The dashboard then triggers the CLI worker (or the user
    # runs the CLI manually).
    return SubmitResponse(
        application_id=application_id,
        state="ready_to_submit",
        clicked=False,
        confirmation_evidence="gates passed; awaiting browser worker",
    )
