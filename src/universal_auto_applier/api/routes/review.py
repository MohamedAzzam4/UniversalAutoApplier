"""Review-before-submit API.

Per ROADMAP WP 5.3/6.3: show review state and allow explicit approve/deny.
The review state is in-memory for Phase 6; the API stores the latest
review state on app.state so the dashboard can query it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from universal_auto_applier.interventions.review import (
    ReviewState,
    approve_review_state,
    check_submit_approval,
)

router = APIRouter(tags=["review"])


class ReviewStateResponse(BaseModel):
    """Review state for the API response."""

    application_id: str
    approved: bool
    can_submit: bool
    has_unresolved_interventions: bool
    documents: list[str] = []
    unanswered_fields: list[str] = []
    final_action_detected: str | None = None
    fill_summary: dict[str, Any] | None = None


class ApproveRequest(BaseModel):
    """Request to approve a review state."""

    approval_id: str


class SubmitCheckResponse(BaseModel):
    """Response for submit approval check."""

    can_submit: bool
    reason: str = ""


@router.get("/review/{application_id}", response_model=ReviewStateResponse)
def get_review_state(request: Request, application_id: str) -> ReviewStateResponse:
    """Return the current review state for a job.

    Phase 6: the review state is stored in-memory on app.state.review_states,
    keyed by application_id. If no review state exists, returns a default
    unapproved state.
    """
    app = request.app
    states: dict[str, ReviewState] = getattr(app.state, "review_states", {})
    state = states.get(application_id)

    if state is None:
        return ReviewStateResponse(
            application_id=application_id,
            approved=False,
            can_submit=False,
            has_unresolved_interventions=False,
        )

    return ReviewStateResponse(
        application_id=state.application_id,
        approved=state.approved,
        can_submit=state.can_submit,
        has_unresolved_interventions=state.has_unresolved_interventions,
        documents=state.documents,
        unanswered_fields=state.unanswered_fields,
        final_action_detected=state.final_action_detected,
        fill_summary=state.fill_summary.model_dump() if state.fill_summary else None,
    )


@router.post("/review/{application_id}/approve")
def approve_review_endpoint(
    request: Request,
    application_id: str,
    body: ApproveRequest,
) -> dict[str, Any]:
    """Approve a review state for submission.

    This does NOT submit the application. It only sets the approval flag.
    The actual submit is performed by the pipeline orchestrator (Phase 8)
    after checking ``check_submit_approval``.

    If there are unresolved interventions, the approval is rejected.
    """
    app = request.app
    states: dict[str, ReviewState] = getattr(app.state, "review_states", {})
    state = states.get(application_id)

    if state is None:
        raise HTTPException(status_code=404, detail="No review state found for this job")

    try:
        approve_review_state(state, approval_id=body.approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Store the updated state back.
    states[application_id] = state

    return {"status": "approved", "application_id": application_id, "approval_id": body.approval_id}


@router.post("/review/{application_id}/deny")
def deny_review_endpoint(
    request: Request,
    application_id: str,
) -> dict[str, Any]:
    """Deny (revoke) a review state approval."""
    app = request.app
    states: dict[str, ReviewState] = getattr(app.state, "review_states", {})
    state = states.get(application_id)

    if state is None:
        raise HTTPException(status_code=404, detail="No review state found for this job")

    state.approved = False
    state.approval_id = None
    state.approved_at = None

    return {"status": "denied", "application_id": application_id}


@router.get("/review/{application_id}/submit-check", response_model=SubmitCheckResponse)
def submit_check_endpoint(request: Request, application_id: str) -> SubmitCheckResponse:
    """Check whether submission is allowed for a job.

    This is the hard safety gate. Returns ``can_submit: false`` unless:
    1. A review state exists.
    2. The review state is approved.
    3. No unresolved interventions remain.
    """
    app = request.app
    states: dict[str, ReviewState] = getattr(app.state, "review_states", {})
    state = states.get(application_id)

    allowed = check_submit_approval(state)

    reason = ""
    if state is None:
        reason = "No review state found"
    elif not state.approved:
        reason = "Review state not approved"
    elif state.has_unresolved_interventions:
        reason = "Unresolved interventions remain"

    return SubmitCheckResponse(can_submit=allowed, reason=reason)
