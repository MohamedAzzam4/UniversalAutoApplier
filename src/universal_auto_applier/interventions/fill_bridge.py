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
            question = _make_question(result)
            options = _make_options(result)

            create_intervention(
                session,
                application_id=application_id,
                kind=kind,
                question=question,
                options=options,
                suggested_answer=result.value,
                confidence=result.confidence,
                field_selector=result.field_selector,
                page_url=page_url,
                screenshot=screenshot,
            )
            count += 1

        elif result.status == "blocked":
            # Blocked fields (e.g. password) also need an intervention
            # so the user knows they were blocked.
            kind = InterventionKind.FIELD_ANSWER
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


def _make_question(result: FillResult) -> str:
    """Create a human-readable question from a FillResult."""
    if result.field_type == "file":
        return f"Missing document for field: {result.explanation}"
    if result.confidence > 0 and result.value:
        return f"Low-confidence answer for field: {result.explanation}"
    return f"Unknown required field: {result.explanation}"


def _make_options(result: FillResult) -> list[str]:
    """Create options list from a FillResult (empty for now)."""
    return []


__all__ = ["create_interventions_from_fill_summary"]
