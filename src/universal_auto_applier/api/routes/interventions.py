"""Intervention API.

Per ROADMAP WP 6.3: show pending interventions, allow approve/edit/skip/block/resolve.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from universal_auto_applier.core.statuses import InterventionStatus

router = APIRouter(tags=["interventions"])


class InterventionResponse(BaseModel):
    """An intervention in the API response."""

    intervention_id: str
    application_id: str
    status: str
    kind: str
    question: str
    options: list[str] = []
    suggested_answer: str | None = None
    confidence: float | None = None
    field_selector: str | None = None
    page_url: str | None = None
    screenshot: str | None = None
    created_at: str = ""
    resolved_at: str | None = None


class InterventionListResponse(BaseModel):
    total: int
    interventions: list[InterventionResponse]


class ResolveRequest(BaseModel):
    """Request body for resolving an intervention."""

    resolution: str  # approved, edited, skipped, blocked, resolved
    answer: str | None = None
    save_to_memory: bool = False


@router.get("/interventions", response_model=InterventionListResponse)
def list_interventions(
    request: Request,
    application_id: str | None = None,
    pending_only: bool = True,
) -> InterventionListResponse:
    """List interventions, optionally filtered."""
    from universal_auto_applier.interventions.store import (
        list_all_interventions,
        list_pending_interventions,
    )

    app = request.app
    session_factory = app.state.session_factory

    with session_factory() as session:
        if pending_only:
            interventions = list_pending_interventions(session, application_id)
        else:
            interventions = list_all_interventions(session, application_id)

    return InterventionListResponse(
        total=len(interventions),
        interventions=[
            InterventionResponse(
                intervention_id=i.intervention_id,
                application_id=i.application_id,
                status=str(i.status),
                kind=str(i.kind),
                question=i.question,
                options=i.options,
                suggested_answer=i.suggested_answer,
                confidence=i.confidence,
                field_selector=i.field_selector,
                page_url=i.page_url,
                screenshot=i.screenshot,
                created_at=i.created_at.isoformat() if i.created_at else "",
                resolved_at=i.resolved_at.isoformat() if i.resolved_at else None,
            )
            for i in interventions
        ],
    )


@router.post("/interventions/{intervention_id}/resolve")
def resolve_intervention_endpoint(
    request: Request,
    intervention_id: str,
    body: ResolveRequest,
) -> dict[str, Any]:
    """Resolve an intervention with a user decision.

    If ``save_to_memory`` is True and an answer is provided, stores the
    answer in answer memory for future reuse.
    """
    from universal_auto_applier.interventions.answer_memory import store_answer
    from universal_auto_applier.interventions.store import (
        get_intervention,
        resolve_intervention,
    )

    app = request.app
    session_factory = app.state.session_factory

    try:
        resolution = InterventionStatus(body.resolution)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid resolution: {body.resolution}")

    with session_factory() as session:
        # Check the intervention exists.
        existing = get_intervention(session, intervention_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Intervention not found")

        resolve_intervention(
            session,
            intervention_id,
            resolution=resolution,
            answer=body.answer,
        )

        # Save to answer memory if requested.
        if body.save_to_memory and body.answer:
            store_answer(
                session,
                question=existing.question,
                answer=body.answer,
                source="user_confirmed",
            )

        session.commit()

    return {"status": "resolved", "intervention_id": intervention_id, "resolution": body.resolution}
