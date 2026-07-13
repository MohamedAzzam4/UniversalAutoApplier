"""Fill engine — fill form fields safely, never submit.

Per ``ROADMAP.md`` WP 4.3:
- Fill fields by control type.
- After filling, detect validation errors (deferred to Phase 5+ with browser).
- Save evidence before and after filling (deferred; Phase 4 is fixture-only).
- Required fields are reported when missing.
- File upload paths are validated before upload.

Safety:
- Never clicks submit buttons.
- Never fills password fields.
- Never bypasses login, captcha, or consent flows.
- Required unknown fields create interventions.
- File fields map only to known documents (cv_pdf, cover_letter_pdf).
- The output makes clear which fields were filled, skipped, blocked, or
  require human input.
"""

from __future__ import annotations

import logging
from pathlib import Path

from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FillResult,
    FormField,
    FormFillSummary,
)
from universal_auto_applier.form_engine.field_mapper import (
    CONFIDENCE_THRESHOLD,
    map_field,
)

logger = logging.getLogger("universal_auto_applier.form_engine.fill_engine")


def fill_form(
    fields: list[FormField],
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> FormFillSummary:
    """Fill a form's fields with candidate and job data.

    This is the main entry point for the fill engine. It:
    1. Maps each field to a value using deterministic rules.
    2. Fills fields with confident mappings.
    3. Skips fields without mappings (optional fields).
    4. Blocks password fields.
    5. Creates intervention needs for required unknown fields.
    6. Validates file paths before filling file fields.
    7. Never submits the form.

    Args:
        fields: The form fields extracted from the page.
        candidate: The candidate profile data.
        job: The application job (for document paths).

    Returns:
        A :class:`FormFillSummary` with per-field results.
    """
    summary = FormFillSummary(total_fields=len(fields))

    for field in fields:
        result = _fill_single_field(field, candidate, job)
        summary.results.append(result)

        if result.status == "filled":
            summary.filled += 1
        elif result.status == "skipped":
            summary.skipped += 1
        elif result.status == "blocked":
            summary.blocked += 1
        elif result.status == "intervention_needed":
            summary.intervention_needed += 1

    return summary


def _fill_single_field(
    field: FormField,
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> FillResult:
    """Fill a single field and return the result.

    The result status is one of:
    - ``filled``: the field was mapped and filled successfully.
    - ``skipped``: the field is optional and no mapping was found.
    - ``blocked``: the field is a password field or otherwise unsafe.
    - ``intervention_needed``: the field is required but no mapping was found,
      or the mapping confidence is below threshold.
    """
    # Block password fields.
    if _is_password_field(field):
        return FillResult(
            field_selector=field.selector,
            status="blocked",
            explanation="Password fields are never filled",
        )

    # Block unknown type fields (safety).
    if field.type == "unknown" and not _is_password_field(field):
        if field.required:
            return FillResult(
                field_selector=field.selector,
                status="intervention_needed",
                explanation="Required field has unknown type and no mapping",
            )
        return FillResult(
            field_selector=field.selector,
            status="skipped",
            explanation="Optional field has unknown type",
        )

    # Try to map the field.
    mapping = map_field(field, candidate, job)

    if mapping is None:
        # No mapping found.
        if field.required:
            return FillResult(
                field_selector=field.selector,
                status="intervention_needed",
                explanation="Required field has no deterministic mapping",
            )
        return FillResult(
            field_selector=field.selector,
            status="skipped",
            explanation="Optional field has no mapping",
        )

    # Check confidence threshold.
    if mapping.confidence < CONFIDENCE_THRESHOLD:
        if field.required:
            return FillResult(
                field_selector=field.selector,
                status="intervention_needed",
                value=mapping.value,
                source=mapping.source,
                confidence=mapping.confidence,
                explanation=f"Low confidence ({mapping.confidence}): {mapping.explanation}",
            )
        return FillResult(
            field_selector=field.selector,
            status="skipped",
            value=mapping.value,
            source=mapping.source,
            confidence=mapping.confidence,
            explanation=f"Low confidence optional field: {mapping.explanation}",
        )

    # For file fields, validate the path exists.
    if field.type == "file":
        if not Path(mapping.value).exists():
            return FillResult(
                field_selector=field.selector,
                status="intervention_needed",
                value=mapping.value,
                source=mapping.source,
                confidence=mapping.confidence,
                explanation=f"File does not exist: {mapping.value}",
            )

    # Field is mapped with sufficient confidence.
    return FillResult(
        field_selector=field.selector,
        status="filled",
        value=mapping.value,
        source=mapping.source,
        confidence=mapping.confidence,
        explanation=mapping.explanation,
    )


def _is_password_field(field: FormField) -> bool:
    """Check if a field is a password field."""
    label_lower = field.label.lower()
    name_lower = field.name.lower()
    nearby_lower = field.nearby_text.lower()
    return (
        "password" in label_lower
        or "password" in name_lower
        or "password" in nearby_lower
        or "passwort" in label_lower
        or "passwort" in name_lower
    )


__all__ = ["fill_form"]
