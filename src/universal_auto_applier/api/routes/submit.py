"""Controlled final submission API.

Endpoints:
- POST /api/submit/{application_id}/observe — run live observation, persist snapshot
- GET  /api/submit/{application_id}/status  — return complete persisted snapshot
- POST /api/submit/{application_id}/confirm-high-risk — confirm high-risk fields
- POST /api/submit/{application_id}/approve — approve the persisted snapshot
- POST /api/submit/{application_id}/revoke  — revoke approval
- POST /api/submit/{application_id}/submit  — execute controlled submit

All responses use typed Pydantic models. The dashboard never constructs
snapshots from in-memory ReviewState — it receives everything from the
persisted observation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from universal_auto_applier.api.models.submission import (
    ApproveRequest,
    ApproveResponse,
    ConfirmHighRiskRequest,
    ConfirmHighRiskResponse,
    LiveReviewDocument,
    LiveReviewField,
    LiveReviewSnapshotResponse,
    LiveReviewSubmitControl,
    ObserveResponse,
    RevokeResponse,
    StatusResponse,
    SubmitRequest,
    SubmitResponse,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.interventions.store import count_pending_interventions
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import get_application_job
from universal_auto_applier.persistence.models import SubmissionApprovalRow, SubmissionResultRow
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import SubmissionSnapshot
from universal_auto_applier.submission.store import (
    confirm_high_risk_fields,
    get_active_approval,
    get_latest_result,
)

router = APIRouter(tags=["submit"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_snapshot_response(
    settings: Settings,
    application_id: str,
    snapshot: SubmissionSnapshot | None = None,
    approval: SubmissionApprovalRow | None = None,
    latest_result: SubmissionResultRow | None = None,
    job: Any = None,
) -> LiveReviewSnapshotResponse:
    """Build the complete typed snapshot response from persisted data."""
    # Get job info if available.
    company = ""
    job_title = ""
    external_job_id = ""
    platform = ""
    application_url = ""
    if job is not None:
        company = getattr(job, "company", "") or ""
        job_title = getattr(job, "title", "") or ""
        external_job_id = getattr(job, "external_job_id", "") or ""
        platform = str(getattr(job, "platform", "")) or ""
        application_url = getattr(job, "url", "") or ""

    # If no snapshot, return empty response.
    if snapshot is None:
        return LiveReviewSnapshotResponse(
            application_id=application_id,
            company=company,
            job_title=job_title,
            external_job_id=external_job_id,
            platform=platform,
            application_url=application_url,
            enable_real_submission=settings.enable_real_submission,
            latest_submission_state=str(latest_result.state) if latest_result else None,
            latest_submission_error=latest_result.error_message
            if latest_result and latest_result.error_message
            else None,
            latest_submission_timestamp=latest_result.attempted_at if latest_result else None,
        )

    # Build field list.
    confirmed_tokens: set[str] = set()
    if approval is not None:
        confirmed_tokens = set(approval.confirmed_high_risk_fields_json or [])

    fields: list[LiveReviewField] = []
    for f in snapshot.fields:
        is_high_risk = f.requires_confirmation or f.risk_level.lower() == "high"
        is_confirmed = f.field_token in confirmed_tokens if is_high_risk else False
        fields.append(
            LiveReviewField(
                field_token=f.field_token,
                label=f.label,
                field_type=f.field_type,
                required=f.required,
                filled_value=f.filled_value,
                selected_value=f.selected_value,
                status=f.status,
                risk_level=f.risk_level,
                requires_confirmation=f.requires_confirmation,
                confirmed=is_confirmed,
                evidence="",
                source="",
                options=[],
                validation_error="",
            )
        )

    # Build document list.
    documents: list[LiveReviewDocument] = []
    for d in snapshot.documents:
        filename = Path(d.path).name if d.path else ""
        file_exists = False
        file_readable = False
        if d.path:
            p = Path(d.path)
            file_exists = p.exists()
            file_readable = file_exists and p.is_file()
        documents.append(
            LiveReviewDocument(
                document_kind=d.document_kind,
                filename=filename,
                path=d.path,
                content_hash=d.content_hash,
                exists=file_exists,
                readable=file_readable,
            )
        )

    # Build submit control.
    submit_control = None
    if snapshot.submit_control is not None:
        submit_control = LiveReviewSubmitControl(
            text=snapshot.submit_control.text,
            selector=snapshot.submit_control.selector,
            frame_url=snapshot.submit_control.frame_url,
        )

    # Determine approval state.
    approval_state = "none"
    approved_snapshot_hash = None
    approval_is_stale = False
    if approval is not None:
        if approval.consumed_at is not None:
            approval_state = "consumed"
        elif approval.revoked_at is not None:
            approval_state = "revoked"
        else:
            approval_state = "active"
        approved_snapshot_hash = approval.snapshot_hash
        if approval.snapshot_hash != snapshot.snapshot_hash:
            approval_is_stale = True

    # Determine can_approve.
    can_approve = True
    approve_blocking_reason = ""
    if snapshot.unresolved_required_field_count > 0:
        can_approve = False
        approve_blocking_reason = (
            f"{snapshot.unresolved_required_field_count} unresolved required fields"
        )
    elif snapshot.pending_intervention_count > 0:
        can_approve = False
        approve_blocking_reason = f"{snapshot.pending_intervention_count} pending interventions"
    else:
        unconfirmed = sum(
            1
            for f in snapshot.fields
            if (f.requires_confirmation or f.risk_level.lower() == "high")
            and f.field_token not in confirmed_tokens
        )
        if unconfirmed > 0:
            can_approve = False
            approve_blocking_reason = f"{unconfirmed} unconfirmed high-risk answers"

    # Determine can_submit.
    can_submit = approval_state == "active" and not approval_is_stale and can_approve
    submit_blocking_reason = ""
    if not can_submit:
        if approval_state != "active":
            submit_blocking_reason = f"approval state is {approval_state}"
        elif approval_is_stale:
            submit_blocking_reason = "approval is stale"
        else:
            submit_blocking_reason = approve_blocking_reason

    return LiveReviewSnapshotResponse(
        application_id=application_id,
        external_job_id=external_job_id,
        company=company,
        job_title=job_title,
        application_url=snapshot.application_url or application_url,
        platform=platform,
        observation_timestamp=snapshot.created_at,
        form_fingerprint=snapshot.form_fingerprint,
        snapshot_hash=snapshot.snapshot_hash,
        is_complete=snapshot.unresolved_required_field_count == 0,
        is_stale=approval_is_stale,
        submit_control=submit_control,
        fields=fields,
        documents=documents,
        pending_intervention_count=snapshot.pending_intervention_count,
        unresolved_required_field_count=snapshot.unresolved_required_field_count,
        unconfirmed_high_risk_count=sum(
            1
            for f in snapshot.fields
            if (f.requires_confirmation or f.risk_level.lower() == "high")
            and f.field_token not in confirmed_tokens
        ),
        active_approval_id=approval.approval_id
        if approval and approval_state == "active"
        else None,
        approval_state=approval_state,
        approved_snapshot_hash=approved_snapshot_hash,
        approval_is_stale=approval_is_stale,
        can_approve=can_approve,
        approve_blocking_reason=approve_blocking_reason,
        can_submit=can_submit,
        submit_blocking_reason=submit_blocking_reason,
        enable_real_submission=settings.enable_real_submission,
        latest_submission_state=str(latest_result.state) if latest_result else None,
        latest_submission_error=latest_result.error_message
        if latest_result and latest_result.error_message
        else None,
        latest_submission_timestamp=latest_result.attempted_at if latest_result else None,
    )


def _load_snapshot_from_approval(approval: SubmissionApprovalRow) -> SubmissionSnapshot | None:
    """Load the snapshot from the approval's snapshot_json column."""
    raw = approval.snapshot_json
    if not raw:
        return None
    try:
        data = approval.snapshot_json
        if isinstance(data, str):
            data = json.loads(data)
        return SubmissionSnapshot.model_validate(data)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/submit/{application_id}/observe")
def observe_snapshot_endpoint(
    request: Request,
    application_id: str,
) -> ObserveResponse:
    """Run live observation and persist the complete snapshot.

    Launches the browser, navigates to the application URL, fills the form,
    and builds a complete snapshot from the live page. The snapshot is
    persisted as an unapproved approval row.

    Does NOT approve. Does NOT submit. Returns the complete persisted
    snapshot so the dashboard can render it.
    """
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    context_factory = getattr(app.state, "submission_context_factory", None)
    if context_factory is None:
        raise HTTPException(
            status_code=503,
            detail="no browser context factory registered; cannot observe live form",
        )

    from universal_auto_applier.submission.execution_service import (
        SubmissionExecutionService,
    )

    service = SubmissionExecutionService(settings, session_factory, context_factory)
    snapshot = service.observe_and_persist_snapshot(application_id=application_id)

    if snapshot is None:
        raise HTTPException(
            status_code=500,
            detail="failed to observe live form (check logs for details)",
        )

    # Load the full state for the response.
    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        latest = get_latest_result(session, application_id)
        job = get_application_job(session, application_id)

    resp = _build_snapshot_response(settings, application_id, snapshot, approval, latest, job)
    return ObserveResponse(snapshot=resp)


@router.get("/submit/{application_id}/status")
def get_submission_status(request: Request, application_id: str) -> StatusResponse:
    """Return the complete persisted snapshot and all safety/approval/result state.

    Works after process restart. Does not depend on in-memory ReviewState.
    """
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        latest = get_latest_result(session, application_id)
        job = get_application_job(session, application_id)

    # Load the snapshot from the approval's snapshot_json.
    snapshot = None
    if approval is not None:
        snapshot = _load_snapshot_from_approval(approval)

    resp = _build_snapshot_response(settings, application_id, snapshot, approval, latest, job)
    return StatusResponse(snapshot=resp)


@router.post("/submit/{application_id}/confirm-high-risk")
def confirm_high_risk_endpoint(
    request: Request,
    application_id: str,
    body: ConfirmHighRiskRequest,
) -> ConfirmHighRiskResponse:
    """Confirm high-risk fields for the current snapshot.

    Rules:
    - Reject confirmation for a stale snapshot (snapshot_hash mismatch).
    - Reject unknown field tokens.
    - Reject tokens that are not high-risk/confirmation-required fields.
    - Persist confirmation against the exact current snapshot.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true (deliberate confirmation required)",
        )

    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        if approval is None:
            raise HTTPException(
                status_code=404,
                detail="no active approval; observe first to create a snapshot",
            )

        # Check snapshot hash matches.
        if approval.snapshot_hash != body.snapshot_hash:
            raise HTTPException(
                status_code=409,
                detail="snapshot hash mismatch; the snapshot has changed since observation",
            )

        # Load the snapshot to verify field tokens.
        snapshot = _load_snapshot_from_approval(approval)
        if snapshot is None:
            raise HTTPException(
                status_code=500,
                detail="failed to load snapshot from approval",
            )

        # Validate field tokens.
        field_map = {f.field_token: f for f in snapshot.fields}
        valid_high_risk_tokens = {
            token
            for token, f in field_map.items()
            if f.requires_confirmation or f.risk_level.lower() == "high"
        }
        unknown_tokens = set(body.field_tokens) - set(field_map.keys())
        if unknown_tokens:
            raise HTTPException(
                status_code=400,
                detail=f"unknown field tokens: {sorted(unknown_tokens)}",
            )
        non_high_risk_tokens = set(body.field_tokens) - valid_high_risk_tokens
        if non_high_risk_tokens:
            raise HTTPException(
                status_code=400,
                detail=f"tokens are not high-risk: {sorted(non_high_risk_tokens)}",
            )

        # Persist confirmation.
        confirm_high_risk_fields(session, approval.approval_id, body.field_tokens)

        # Reload for response.
        approval = get_active_approval(session, application_id)
        latest = get_latest_result(session, application_id)
        job = get_application_job(session, application_id)

    snapshot = _load_snapshot_from_approval(approval) if approval else None
    resp = _build_snapshot_response(settings, application_id, snapshot, approval, latest, job)
    return ConfirmHighRiskResponse(
        snapshot=resp,
        confirmed_tokens=body.field_tokens,
    )


@router.post("/submit/{application_id}/approve")
def approve_snapshot_endpoint(
    request: Request,
    application_id: str,
    body: ApproveRequest,
) -> ApproveResponse:
    """Approve the persisted snapshot.

    Accepts the snapshot_hash (not an arbitrary client-built snapshot).
    Rejects:
    - Incomplete snapshots (unresolved required fields).
    - Pending interventions.
    - Unconfirmed high-risk fields.
    - Stale snapshots (hash mismatch).
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true (deliberate confirmation required)",
        )

    app = request.app
    session_factory = app.state.session_factory

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        if approval is None:
            raise HTTPException(
                status_code=404,
                detail="no active approval; observe first to create a snapshot",
            )

        # Check snapshot hash matches.
        if approval.snapshot_hash != body.snapshot_hash:
            raise HTTPException(
                status_code=409,
                detail="snapshot hash mismatch; the snapshot has changed since observation",
            )

        # Load snapshot for validation.
        snapshot = _load_snapshot_from_approval(approval)
        if snapshot is None:
            raise HTTPException(
                status_code=500,
                detail="failed to load snapshot from approval",
            )

        # Validate gates.
        if snapshot.unresolved_required_field_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"cannot approve: {snapshot.unresolved_required_field_count} unresolved required fields",
            )
        pending = count_pending_interventions(session, application_id)
        if pending > 0:
            raise HTTPException(
                status_code=409,
                detail=f"cannot approve: {pending} pending interventions",
            )
        confirmed_tokens = set(approval.confirmed_high_risk_fields_json or [])
        unconfirmed = sum(
            1
            for f in snapshot.fields
            if (f.requires_confirmation or f.risk_level.lower() == "high")
            and f.field_token not in confirmed_tokens
        )
        if unconfirmed > 0:
            raise HTTPException(
                status_code=409,
                detail=f"cannot approve: {unconfirmed} unconfirmed high-risk answers",
            )

        # The approval already exists (created by observe). It is now
        # "approved" in the sense that all gates pass. The approval_id
        # is the one returned by observe.
        approval_id = approval.approval_id

    return ApproveResponse(
        application_id=application_id,
        approval_id=approval_id,
        snapshot_hash=body.snapshot_hash,
    )


@router.post("/submit/{application_id}/revoke")
def revoke_approval_endpoint(
    request: Request,
    application_id: str,
) -> RevokeResponse:
    """Revoke the active approval idempotently."""
    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    with session_scope(session_factory) as session:
        approval = get_active_approval(session, application_id)
        if approval is None:
            # Idempotent: return success even if no approval exists.
            return RevokeResponse(application_id=application_id, revoked=True)
        approval_id = approval.approval_id

    coordinator = SubmissionCoordinator(settings, session_factory)
    coordinator.revoke_approval(approval_id)
    return RevokeResponse(application_id=application_id, revoked=True)


@router.post("/submit/{application_id}/submit")
def submit_endpoint(
    request: Request,
    application_id: str,
    body: SubmitRequest,
) -> SubmitResponse:
    """Execute the controlled final submission."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true to submit (deliberate confirmation required)",
        )

    app = request.app
    settings = app.state.settings
    session_factory = app.state.session_factory

    context_factory = getattr(app.state, "submission_context_factory", None)

    if context_factory is not None:
        from universal_auto_applier.submission.execution_service import (
            SubmissionExecutionService,
        )

        service = SubmissionExecutionService(settings, session_factory, context_factory)
        artifact_dir = settings.data_dir / "live-runs" / f"{application_id[:12]}-submit"
        result = service.execute_controlled_submission(
            application_id=application_id,
            approval_id=body.approval_id,
            artifact_dir=artifact_dir,
        )
        return SubmitResponse(
            application_id=application_id,
            state=str(result.state),
            clicked=result.clicked,
            confirmation_evidence=result.confirmation_evidence,
            error_message=result.error_message,
        )
    else:
        coordinator = SubmissionCoordinator(settings, session_factory)
        gate = coordinator.check_gates(application_id=application_id)
        if not gate.allowed:
            return SubmitResponse(
                application_id=application_id,
                state=str(gate.state),
                clicked=False,
                error_message=gate.reason,
            )
        return SubmitResponse(
            application_id=application_id,
            state="ready_to_submit",
            clicked=False,
            confirmation_evidence="gates passed; no browser context factory registered",
        )
