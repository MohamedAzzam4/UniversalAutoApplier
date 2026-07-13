"""Field mapper — deterministic field-to-value mapping.

Per ``ROADMAP.md`` WP 4.2, maps form fields to candidate/job data using
deterministic rules first. AI is only for ambiguous fields (not in Phase 4).

Every mapping returns:
- value
- source
- confidence
- explanation

Rules:
- Low-confidence mappings (below threshold) create interventions.
- File fields map only to existing files (cv_pdf, cover_letter_pdf).
- Required unknown fields create interventions.
- Password fields are never mapped.

The confidence threshold for auto-fill is 0.7. Below that, the field is
marked as requiring user confirmation.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldMapping,
    FormField,
)

logger = logging.getLogger("universal_auto_applier.form_engine.field_mapper")

# Confidence threshold for auto-fill without user confirmation.
CONFIDENCE_THRESHOLD = 0.7

# Field label patterns for deterministic mapping.
# Each pattern is (regex, source_field, explanation).
# The regex is matched against the label (case-insensitive).
_LABEL_PATTERNS: list[tuple[str, str, str]] = [
    # Name fields
    (r"^first\s*name$", "first_name", "Label matched 'first name'"),
    (r"^last\s*name$", "last_name", "Label matched 'last name'"),
    (r"^full\s*name$", "full_name", "Label matched 'full name'"),
    (r"^name$", "full_name", "Label matched 'name' (assumed full name)"),
    # Contact fields
    (r"^email", "email", "Label matched 'email'"),
    (r"^e-mail", "email", "Label matched 'e-mail'"),
    (r"^phone", "phone", "Label matched 'phone'"),
    (r"^mobile", "phone", "Label matched 'mobile'"),
    (r"^tel", "phone", "Label matched 'tel'"),
    # URLs
    (r"linkedin", "linkedin_url", "Label matched 'linkedin'"),
    (r"github", "github_url", "Label matched 'github'"),
    (r"website", "website", "Label matched 'website'"),
    (r"^url$", "website", "Label matched 'url' (assumed website)"),
    (r"portfolio", "website", "Label matched 'portfolio' (assumed website)"),
    # Location
    (r"^city$", "city", "Label matched 'city'"),
    (r"^country$", "country", "Label matched 'country'"),
    (r"location", "city", "Label matched 'location' (assumed city)"),
    # Experience
    (r"years.*experience", "years_of_experience", "Label matched 'years of experience'"),
    (r"experience", "years_of_experience", "Label matched 'experience'"),
    (r"current.*position", "current_position", "Label matched 'current position'"),
    (r"current.*title", "current_position", "Label matched 'current title'"),
    (r"current.*role", "current_position", "Label matched 'current role'"),
    # Sponsorship / work authorization
    (r"sponsorship", "requires_sponsorship", "Label matched 'sponsorship'"),
    (r"visa", "requires_sponsorship", "Label matched 'visa'"),
    (r"work.*authorization", "work_authorization", "Label matched 'work authorization'"),
    (r"authorized.*work", "work_authorization", "Label matched 'authorized to work'"),
]

# File field patterns.
_FILE_FIELD_PATTERNS: list[tuple[str, str, str]] = [
    (r"resume|cv", "cv_pdf", "File field matched 'resume/cv'"),
    (r"cover.*letter", "cover_letter_pdf", "File field matched 'cover letter'"),
]


def _normalize_label(label: str) -> str:
    """Normalize a label for matching."""
    return label.strip().lower()


def _try_match_label(
    field: FormField,
) -> tuple[str, str, str] | None:
    """Try to match a field's label against known patterns.

    Returns (source_field, source_name, explanation) or None.
    """
    label = _normalize_label(field.label)
    nearby = _normalize_label(field.nearby_text)

    for text in (label, nearby):
        if not text:
            continue
        for pattern, source_field, explanation in _LABEL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return source_field, explanation, "candidate_profile"

    return None


def _try_match_file_field(field: FormField) -> tuple[str, str] | None:
    """Try to match a file field to a document type.

    Returns (job_field, explanation) or None.
    """
    label = _normalize_label(field.label)
    nearby = _normalize_label(field.nearby_text)
    name = _normalize_label(field.name)

    for text in (label, nearby, name):
        if not text:
            continue
        for pattern, job_field, explanation in _FILE_FIELD_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return job_field, explanation

    return None


def map_field(
    field: FormField,
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> FieldMapping | None:
    """Map a single form field to a value.

    Args:
        field: The form field to map.
        candidate: The candidate profile data.
        job: The application job (for document paths).

    Returns:
        A :class:`FieldMapping` if the field can be mapped, or None if no
        mapping is found (caller should create an intervention for required
        fields).

    Safety:
    - Password fields are never mapped.
    - File fields only map to cv_pdf or cover_letter_pdf from the job.
    - File paths must exist on disk.
    """
    # Never map password fields.
    if field.type == "unknown" and "password" in _normalize_label(field.label):
        return None

    # Handle file fields.
    if field.type == "file":
        file_match = _try_match_file_field(field)
        if file_match is None:
            return None
        job_field, explanation = file_match
        file_path = getattr(job, job_field, None)
        if file_path is None or not file_path:
            return None
        # Validate that the file exists.
        if not Path(file_path).exists():
            return FieldMapping(
                field_selector=field.selector,
                value=file_path,
                source="application_job",
                confidence=0.5,
                requires_user_confirmation=True,
                explanation=f"{explanation}, but file does not exist: {file_path}",
            )
        return FieldMapping(
            field_selector=field.selector,
            value=file_path,
            source="document_path",
            confidence=0.99,
            requires_user_confirmation=False,
            explanation=explanation,
        )

    # Try deterministic label matching.
    match = _try_match_label(field)
    if match is None:
        return None

    source_field, explanation, source_name = match

    # Get the value from the candidate profile.
    value = getattr(candidate, source_field, None)
    if value is None:
        return None

    # Convert bool to string for radio/checkbox/select.
    if isinstance(value, bool):
        value = "Yes" if value else "No"

    confidence = 0.95 if source_name == "candidate_profile" else 0.50

    return FieldMapping(
        field_selector=field.selector,
        value=str(value),
        source=source_name,
        confidence=confidence,
        requires_user_confirmation=confidence < CONFIDENCE_THRESHOLD,
        explanation=explanation,
    )


def map_fields(
    fields: list[FormField],
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> list[FieldMapping]:
    """Map multiple form fields.

    Returns only successful mappings. Fields with no mapping are not included;
    the caller is responsible for checking required fields without mappings.
    """
    mappings: list[FieldMapping] = []
    for field in fields:
        mapping = map_field(field, candidate, job)
        if mapping is not None:
            mappings.append(mapping)
    return mappings


__all__ = [
    "map_field",
    "map_fields",
    "CONFIDENCE_THRESHOLD",
]
