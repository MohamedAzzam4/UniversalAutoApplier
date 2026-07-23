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
    llm_metadata: dict[str, Any] | None = None
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
                llm_metadata=i.llm_metadata,
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
    answer in answer memory for future reuse AND updates the job's
    ``form_answers`` metadata so the deterministic mapper can reuse the
    answer on pipeline retry.

    The field identity is obtained from the intervention's structured
    ``llm_metadata`` (``field_label``). The ``question`` display text is
    never parsed for structured data.
    """
    from universal_auto_applier.interventions.answer_memory import store_answer
    from universal_auto_applier.interventions.store import (
        get_intervention,
        resolve_intervention,
    )
    from universal_auto_applier.persistence.job_repository import (
        get_application_job,
        upsert_application_job,
    )

    app = request.app
    session_factory = app.state.session_factory

    try:
        resolution = InterventionStatus(body.resolution)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid resolution: {body.resolution}"
        ) from None

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
            # Use the structured field_label from llm_metadata as the question
            # identity. This ensures the stored answer can be matched back to
            # the form field without parsing display text.
            field_label = None
            if existing.llm_metadata:
                field_label = existing.llm_metadata.get("field_label")

            question_for_memory = field_label or existing.question
            store_answer(
                session,
                question=question_for_memory,
                answer=body.answer,
                source="user_confirmed",
            )

            # Also update job.metadata.form_answers so the deterministic
            # mapper can reuse the answer on pipeline retry.
            job = get_application_job(session, existing.application_id)
            if job is not None:
                form_answers = dict(job.metadata.get("form_answers", {}) or {})
                # Key by field_label so the deterministic mapper can match
                # via _try_explicit_job_answer (which normalises field labels).
                form_answers[field_label] = body.answer if field_label else body.answer
                job.metadata["form_answers"] = form_answers
                upsert_application_job(session, job)

        session.commit()

    return {"status": "resolved", "intervention_id": intervention_id, "resolution": body.resolution}
