# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportOptionalIterable=false
"""Intervention API.

Per ROADMAP WP 6.3: show pending interventions, allow approve/edit/skip/block/resolve.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, model_validator

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


class DocumentBundleFile(BaseModel):
    """One file in a document bundle for a FILE-field intervention."""

    path: str
    kind: str = "unknown"


class ResolveRequest(BaseModel):
    """Request body for resolving an intervention.

    For FILE fields with an owner-selected document bundle (e.g. CV +
    transcript for Vollständige Bewerbungsunterlagen), the bundle is
    submitted as a structured value rather than a single string. This
    preserves the ordered list and per-file kind so the final snapshot
    can distinguish CV as ``cv``. Scalar text/select/radio answers
    remain ``answer: str`` for backwards compatibility.
    """

    resolution: str  # approved, edited, skipped, blocked, resolved
    answer: Any | None = None  # scalar string for text/select/radio, or structured bundle for FILE
    # Structured file bundle for FILE fields — narrow extension, not for text fields
    file_bundle: list[DocumentBundleFile] | None = None
    save_to_memory: bool = False

    @model_validator(mode="after")
    def _validate_bundle_not_mixed(self) -> ResolveRequest:
        # file_bundle is only for FILE fields; scalar answer and file_bundle are mutually exclusive
        if self.file_bundle is not None and self.answer is not None:
            # Allow answer to be None when bundle is provided, but not both non-None
            # (answer as string vs bundle as structured). If both provided, prefer bundle and require answer to be None.
            # For backwards compat, we allow answer as string when file_bundle is None.
            # If both are set, it's an error — client should send one or the other.
            if isinstance(self.answer, str) and self.answer.strip():
                raise ValueError(
                    "Provide either 'answer' (scalar) or 'file_bundle' (structured), not both"
                )
            if isinstance(self.answer, (dict, list)) and self.answer:
                raise ValueError("Provide either 'answer' or 'file_bundle', not both")
        return self


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

    Semantics (decoupled per the supervisor V0 Phase-0 correction):

    - Accepting resolutions (``approved``/``edited``/``resolved``) ALWAYS
      persist the supplied answer to this job's
      ``metadata.form_answers`` so the deterministic mapper can reuse it
      on retry. This is job-specific persistence and does not depend on
      ``save_to_memory``.
    - ``save_to_memory=True`` ADDITIONALLY stores a REUSABLE scalar answer
      in global AnswerMemory. File bundles are NEVER stored in AnswerMemory
      (global memory does not yet support structured/scoped document
      bundles — storing only the first path would be lossy).
    - Rejecting resolutions (``skipped``/``blocked``) never persist
      supplied data — neither to ``form_answers`` nor to AnswerMemory.

    The field identity is obtained from the intervention's structured
    ``llm_metadata`` (``field_label``). The ``question`` display text is
    never parsed for structured data.
    """
    from universal_auto_applier.interventions.resolve_service import (
        parse_structured_bundle,
        resolve_with_persistence,
    )
    from universal_auto_applier.interventions.store import get_intervention

    app = request.app
    session_factory = app.state.session_factory

    try:
        resolution = InterventionStatus(body.resolution)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid resolution: {body.resolution}"
        ) from None

    # Determine if this is a structured file bundle (for FILE fields)
    # The bundle can arrive via `file_bundle` (preferred typed) or as a
    # structured `answer` (dict with files/paths, or list). Scalar text
    # answers remain `answer: str`. The parsing lives in the shared
    # resolve service so the supervisor tool layer behaves identically.
    structured_bundle: list[dict[str, str]] | None = parse_structured_bundle(
        body.answer,
        None if body.file_bundle is None else [f.model_dump() for f in body.file_bundle],
    )

    with session_factory() as session:
        # Check the intervention exists.
        existing = get_intervention(session, intervention_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Intervention not found")

        # Single shared implementation of the persistence semantics
        # (per-job form_answers always on accepting resolutions;
        # save_to_memory only adds a reusable scalar AnswerMemory entry;
        # skipped/blocked never persist supplied data).
        resolve_with_persistence(
            session,
            intervention=existing,
            resolution=resolution,
            answer=body.answer,
            structured_bundle=structured_bundle,
            save_to_memory=body.save_to_memory,
        )

        session.commit()

    return {"status": "resolved", "intervention_id": intervention_id, "resolution": body.resolution}
