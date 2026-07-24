"""Bridge from Phase 4 fill results to Phase 5 interventions.

This module connects the form fill engine's output (FillResult with
status=intervention_needed or blocked) to the intervention store. It
creates appropriate interventions for:
- Required unknown fields (field_answer)
- Blocked password fields (field_answer with note about password)
- Missing documents (missing_document)
- Low-confidence mappings (field_answer with suggested answer)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from universal_auto_applier.core.models import FillResult, FormFillSummary
from universal_auto_applier.core.statuses import InterventionKind
from universal_auto_applier.interventions.store import create_intervention

logger = logging.getLogger("universal_auto_applier.interventions.bridge")


def create_interventions_from_fill_summary(
    session: Session,
    *,
    application_id: str,
    summary: FormFillSummary,
    page_url: str | None = None,
    screenshot: str | None = None,
) -> int:
    """Create interventions for all fields that need human input.

    For each FillResult with status=intervention_needed or blocked, creates
    an appropriate intervention in the store. Interventions are idempotent
    (creating the same intervention twice returns the existing row).

    Structured field identity (field_label, field_token) is stored in
    ``llm_metadata`` on the intervention. This is the authoritative source
    for the field identity — the ``question`` text is display-only and
    must never be parsed to recover structured data.

    Args:
        session: An open SQLAlchemy session.
        application_id: The job/application ID.
        summary: The form fill summary from the fill engine.
        page_url: URL of the page where filling occurred.
        screenshot: Path to a screenshot, if available.

    Returns:
        The number of interventions created (including pre-existing ones).
    """
    count = 0
    for result in summary.results:
        if result.status == "intervention_needed":
            kind = _determine_kind(result)
            llm_metadata = _build_llm_metadata(result)
            question = _make_question(result)

            create_intervention(
                session,
                application_id=application_id,
                kind=kind,
                question=question,
                options=[],
                suggested_answer=result.value,
                confidence=result.confidence,
                field_selector=result.field_selector,
                page_url=page_url,
                screenshot=screenshot,
                llm_metadata=llm_metadata,
            )
            count += 1

        elif result.status == "blocked":
            kind = InterventionKind.FIELD_ANSWER
            llm_metadata = _build_llm_metadata(result)
            question = f"Field blocked: {result.explanation}"

            create_intervention(
                session,
                application_id=application_id,
                kind=kind,
                question=question,
                options=[],
                suggested_answer=None,
                confidence=0.0,
                field_selector=result.field_selector,
                page_url=page_url,
                screenshot=screenshot,
                llm_metadata=llm_metadata,
            )
            count += 1

    logger.info(
        "[%s] created %d interventions from fill summary",
        application_id[:12],
        count,
    )
    return count


def _determine_kind(result: FillResult) -> InterventionKind:
    """Determine the intervention kind from a FillResult."""
    if result.field_type == "file":
        return InterventionKind.MISSING_DOCUMENT
    if result.field_type == "password":
        return InterventionKind.FIELD_ANSWER
    return InterventionKind.FIELD_ANSWER


def _build_llm_metadata(result: FillResult) -> dict[str, Any]:
    """Build structured metadata for the intervention.

    The returned dict carries the stable field identity so that downstream
    consumers (resolve endpoint, answer memory, pipeline retry) can locate
    the exact form field without parsing human-readable text.
    """
    return {
        "field_label": result.label or "",
        "field_type": result.field_type or "",
    }


def _make_question(result: FillResult) -> str:
    """Create a human-readable question from a FillResult.

    This is display-only text. The structured field identity is carried
    in ``llm_metadata`` and must not be embedded in or parsed from this
    string.
    """
    if result.field_type == "file":
        return f"Missing document for field: {result.explanation}"
    if result.confidence > 0 and result.value:
        return f"Low-confidence answer for field: {result.explanation}"
    return f"Unknown required field: {result.explanation}"


__all__ = ["create_interventions_from_fill_summary"]
