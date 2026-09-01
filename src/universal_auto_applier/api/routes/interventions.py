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

    # Determine if this is a structured file bundle (for FILE fields)
    # The bundle can arrive via `file_bundle` (preferred typed) or as a
    # structured `answer` (dict with files/paths, or list). Scalar text
    # answers remain `answer: str`.
    structured_bundle: list[dict[str, str]] | None = None
    # Prefer explicit file_bundle field
    if body.file_bundle is not None:
        structured_bundle = [{"path": f.path, "kind": f.kind} for f in body.file_bundle]
    elif isinstance(body.answer, dict) and body.answer:
        # Structured dict in answer (e.g. {"files": [...]}) — file bundle
        # We detect this by checking for files/paths/path keys and treat
        # non-file dicts as not bundle (fallback to scalar handling).
        if any(k in body.answer for k in ("files", "paths", "path")):
            # Reuse the same parsing as field_mapper for consistency
            from universal_auto_applier.core.models import DocumentBundleEntry

            # Try to parse as bundle via helper
            tmp_bundle = None
            try:
                # Use the same logic as field_mapper._parse_file_bundle
                # Inline minimal parsing to avoid import cycle
                if "path" in body.answer and isinstance(body.answer["path"], str):
                    p = str(body.answer["path"]).strip()
                    if p:
                        k = str(body.answer.get("kind", "unknown")).strip() or "unknown"
                        tmp_bundle = [DocumentBundleEntry(path=p, kind=k)]
                elif "files" in body.answer and isinstance(body.answer["files"], list):
                    tmp = []
                    for item in body.answer["files"]:
                        if isinstance(item, dict) and "path" in item:
                            p = str(item["path"]).strip()
                            if p:
                                k = str(item.get("kind", "unknown")).strip() or "unknown"
                                tmp.append(DocumentBundleEntry(path=p, kind=k))
                        elif isinstance(item, str) and item.strip():
                            tmp.append(DocumentBundleEntry(path=item.strip(), kind="unknown"))
                    tmp_bundle = tmp if tmp else None
            except Exception:
                tmp_bundle = None
            if tmp_bundle is not None:
                structured_bundle = [{"path": e.path, "kind": e.kind} for e in tmp_bundle]
    elif isinstance(body.answer, list) and body.answer:
        # List in answer — treat as bundle if it looks like file bundle
        # (list of dicts with path, or list of strings)
        is_bundle_like = all(
            isinstance(item, dict) and "path" in item or isinstance(item, str)
            for item in body.answer
        )
        if is_bundle_like:
            bundle_entries: list[dict[str, str]] = []
            for item in body.answer:
                if isinstance(item, dict) and "path" in item:
                    p = str(item["path"]).strip()
                    if p:
                        k = str(item.get("kind", "unknown")).strip() or "unknown"
                        bundle_entries.append({"path": p, "kind": k})
                elif isinstance(item, str) and item.strip():
                    bundle_entries.append({"path": item.strip(), "kind": "unknown"})
            if bundle_entries:
                structured_bundle = bundle_entries

    # For resolve_intervention audit, we need a string answer; for bundles we
    # store the first path as audit string (the structured bundle is persisted
    # in form_answers, not in the intervention's suggested_answer).
    audit_answer: str | None = None
    if structured_bundle is not None:
        # Store first path as audit, but the canonical persistence is form_answers
        audit_answer = structured_bundle[0]["path"] if structured_bundle else None
    elif isinstance(body.answer, str):
        audit_answer = body.answer
    elif body.answer is not None:
        audit_answer = str(body.answer)

    with session_factory() as session:
        # Check the intervention exists.
        existing = get_intervention(session, intervention_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Intervention not found")

        resolve_intervention(
            session,
            intervention_id,
            resolution=resolution,
            answer=audit_answer,
        )

        # Save to answer memory and form_answers if requested.
        # For file bundles, form_answers must retain the structured bundle
        # (not a stringified list) so the mapper can consume it.
        has_bundle = structured_bundle is not None
        has_scalar = isinstance(body.answer, str) and body.answer.strip()
        # Also handle legacy scalar file single path via answer string — but that
        # is already covered by has_scalar.
        if body.save_to_memory and (has_bundle or has_scalar or body.file_bundle is not None):
            # Use the structured field_label from llm_metadata as the question
            # identity. This ensures the stored answer can be matched back to
            # the form field without parsing display text.
            field_label = None
            if existing.llm_metadata:
                field_label = existing.llm_metadata.get("field_label")

            question_for_memory = field_label or existing.question
            # Answer memory is for any answer; for bundles we store the first
            # path as memory (text retrieval), but the mapper uses form_answers.
            memory_answer = None
            if has_bundle:
                memory_answer = structured_bundle[0]["path"]  # type: ignore[index]
            else:
                memory_answer = (
                    body.answer
                    if isinstance(body.answer, str)
                    else str(body.answer)
                    if body.answer
                    else None
                )
            if memory_answer:
                store_answer(
                    session,
                    question=question_for_memory,
                    answer=memory_answer,
                    source="user_confirmed",
                )

            # Also update job.metadata.form_answers so the deterministic
            # mapper can reuse the answer on pipeline retry.
            job = get_application_job(session, existing.application_id)
            if job is not None:
                form_answers = dict(job.metadata.get("form_answers", {}) or {})
                # Key by field_label so the deterministic mapper can match
                # via _try_explicit_job_answer (which normalises field labels).
                # Use field_label if available, otherwise fall back to question.
                key = field_label if field_label else existing.question
                if has_bundle:
                    # Preserve structured bundle: {"files": [{path, kind}, ...]}
                    # This is the canonical typed representation — never a JSON
                    # string, never a delimiter-joined string.
                    form_answers[key] = {"files": structured_bundle}
                else:
                    # Scalar text/select/radio/file-single answer
                    form_answers[key] = body.answer
                job.metadata["form_answers"] = form_answers
                upsert_application_job(session, job)

        session.commit()

    return {"status": "resolved", "intervention_id": intervention_id, "resolution": body.resolution}
