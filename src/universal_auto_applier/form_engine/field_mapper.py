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
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    DocumentBundleEntry,
    FieldMapping,
    FieldOption,
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
    (r"vorname", "first_name", "Label matched German 'Vorname'"),
    (r"nachname|familienname", "last_name", "Label matched German 'Nachname/Familienname'"),
    (r"salutation|anrede", "salutation", "Label matched 'salutation'"),
    (r"academic.*title|akademischer.*titel", "academic_title", "Label matched academic title"),
    # Contact fields
    (r"^email", "email", "Label matched 'email'"),
    (r"^e-mail", "email", "Label matched 'e-mail'"),
    (r"^phone", "phone", "Label matched 'phone'"),
    (r"^mobile", "phone", "Label matched 'mobile'"),
    (r"^tel", "phone", "Label matched 'tel'"),
    (r"telefon|handy|mobiltelefon", "phone", "Label matched German 'Telefon/Handy'"),
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
    (r"\bort\b|wohnort|stadt", "city", "Label matched German 'Ort/Wohnort/Stadt'"),
    (r"\bland\b", "country", "Label matched German 'Land'"),
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

# File field patterns — narrow, high-precision only.
# Generic document labels (Unterlagen, Dokumente, Anlagen,
# Vollständige Bewerbungsunterlagen) are NOT globally mapped to cv_pdf;
# they are ambiguous and must become interventions / owner document
# selection unless target-specific evidence proves an exact mapping.
_FILE_FIELD_PATTERNS: list[tuple[str, str, str]] = [
    (r"resume|cv|lebenslauf", "cv_pdf", "File field matched 'resume/cv/lebenslauf'"),
    (
        r"cover.*letter|anschreiben",
        "cover_letter_pdf",
        "File field matched 'cover letter/anschreiben'",
    ),
]

_QUESTION_ANSWER_KEYS: tuple[str, ...] = (
    "application_answers",
    "form_answers",
    "question_answers",
)


def _normalize_question(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _question_text(field: FormField) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for part in (field.label, field.nearby_text):
        normalized = _normalize_question(part)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parts.append(part)
    if not parts and field.name:
        parts.append(field.name)
    return " ".join(parts)


def _parse_file_bundle(raw_answer: Any) -> list[DocumentBundleEntry] | None:
    """Parse a structured file-bundle answer.

    Accepted structured forms (all JSON-serializable, never a stringified
    list):
    - ``{"files": [{"path": "...", "kind": "cv"}, ...]}``
    - ``{"paths": ["...", "..."]}`` (kinds default to unknown)
    - ``[{"path": "...", "kind": "cv"}, ...]`` (list of dicts)
    - ``["...", "..."]`` (list of path strings, kinds unknown)
    - ``{"path": "...", "kind": "cv"}`` (single dict, treated as one-file bundle)

    Returns a list of bundle entries or None if the value is not a
    structured bundle (caller should treat it as scalar).
    Scalar string answers are NOT parsed here — they remain single-file.
    """
    # Single path string is scalar, not bundle
    if isinstance(raw_answer, str):
        return None
    # Dict forms
    if isinstance(raw_answer, dict):
        raw_dict = cast(dict[str, Any], raw_answer)
        # Already a single file dict?
        if "path" in raw_dict and isinstance(raw_dict["path"], str):
            path = str(raw_dict["path"]).strip()
            if not path:
                return None
            kind = str(raw_dict.get("kind", "unknown")).strip() or "unknown"
            return [DocumentBundleEntry(path=path, kind=kind)]
        # Bundle dict with files/paths key
        files_raw: list[Any] | None = None
        if "files" in raw_dict and isinstance(raw_dict["files"], list):
            files_raw = cast(list[Any], raw_dict["files"])
        elif "paths" in raw_dict and isinstance(raw_dict["paths"], list):
            files_raw = cast(list[Any], raw_dict["paths"])
        else:
            return None
        entries: list[DocumentBundleEntry] = []
        for item in files_raw:
            if isinstance(item, dict) and "path" in cast(dict[str, Any], item):
                item_dict = cast(dict[str, Any], item)
                p = str(item_dict["path"]).strip()
                if not p:
                    continue
                k = str(item_dict.get("kind", "unknown")).strip() or "unknown"
                entries.append(DocumentBundleEntry(path=p, kind=k))
            elif isinstance(item, str):
                p = item.strip()
                if p:
                    entries.append(DocumentBundleEntry(path=p, kind="unknown"))
        return entries if entries else None
    # List forms
    if isinstance(raw_answer, list):
        raw_list = cast(list[Any], raw_answer)
        if not raw_list:
            return None
        entries = []
        for item in raw_list:
            if isinstance(item, dict) and "path" in cast(dict[str, Any], item):
                item_dict = cast(dict[str, Any], item)
                p = str(item_dict["path"]).strip()
                if not p:
                    continue
                k = str(item_dict.get("kind", "unknown")).strip() or "unknown"
                entries.append(DocumentBundleEntry(path=p, kind=k))
            elif isinstance(item, str):
                p = item.strip()
                if p:
                    entries.append(DocumentBundleEntry(path=p, kind="unknown"))
            else:
                return None  # malformed bundle element
        return entries if entries else None
    return None


def _try_explicit_job_answer(field: FormField, job: ApplicationJob) -> FieldMapping | None:
    """Use explicit per-job answers transported in metadata.

    Supported metadata keys are ``application_answers``, ``form_answers``,
    and ``question_answers``. Values come from the user or the upstream
    pipeline; this function never invents an answer.
    """
    normalized_field = _normalize_question(_question_text(field))
    if not normalized_field:
        return None

    for metadata_key in _QUESTION_ANSWER_KEYS:
        raw_answers: Any = job.metadata.get(metadata_key)
        if not isinstance(raw_answers, dict):
            continue
        answers = cast(dict[str, Any], raw_answers)
        # Sort by normalized length descending so more specific (longer) keys are tried first
        # This prevents a short key like "Geburtsdatum" from incorrectly matching a long field
        # whose normalized contains many keywords (e.g., the full privacy policy).
        sorted_answers = sorted(
            answers.items(), key=lambda kv: len(_normalize_question(str(kv[0]))), reverse=True
        )
        for raw_question, raw_answer in sorted_answers:
            if raw_answer is None:
                continue
            normalized_saved = _normalize_question(raw_question)
            if not normalized_saved:
                continue
            # For short labels like PLZ (3 chars), require exact label match to avoid cross-contamination
            # via nearby (e.g., PLZ field's nearby contains "Ort" and vice versa)
            norm_label_only = _normalize_question(field.label)
            label_exact = norm_label_only == normalized_saved
            exact = normalized_saved == normalized_field
            contained = len(normalized_saved) >= 3 and (
                normalized_saved in normalized_field or normalized_field in normalized_saved
            )
            # For very short keys (<=3 chars like PLZ), require label-only exact match to avoid false positives
            if len(normalized_saved) <= 3:
                if not label_exact:
                    continue
            elif not exact and not contained:
                continue
            # FILE fields: check for structured document bundle first
            if field.type == "file":
                bundle = _parse_file_bundle(raw_answer)
                if bundle is not None:
                    # Bundle: preserve ordered list, kinds intact, no stringification
                    # ``value`` is first path for backwards compat; ``document_bundle`` holds full bundle
                    first_path = bundle[0].path if bundle else ""
                    return FieldMapping(
                        field_selector=field.selector,
                        value=first_path,
                        source="application_job",
                        confidence=0.99,
                        requires_user_confirmation=False,
                        explanation=f"Matched explicit file bundle from metadata.{metadata_key} ({len(bundle)} files)",
                        document_bundle=bundle,
                    )
                # Non-file or file scalar: fall through to scalar handling
                # For file scalar, raw_answer must be a string path; non-string bundles already handled
                if not isinstance(raw_answer, str):
                    # Structured non-bundle for non-file field is malformed — fail closed
                    continue
            else:
                # Non-file fields must remain scalar strings — structured bundle is not allowed
                if not isinstance(raw_answer, str):
                    continue
            answer = str(raw_answer).strip()
            if not answer:
                continue
            return FieldMapping(
                field_selector=field.selector,
                value=answer,
                source="application_job",
                confidence=0.99,
                requires_user_confirmation=False,
                explanation=f"Matched explicit answer from metadata.{metadata_key}",
            )
    return None


def _has_yes_no_options(field: FormField) -> bool:
    normalized = {
        _normalize_question(option.label or option.value)
        for option in field.options
        if option.label or option.value
    }
    yes_values = {"yes", "ja", "true"}
    no_values = {"no", "nein", "false"}
    return bool(normalized & yes_values) and bool(normalized & no_values)


def _flatten_evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        mapping = cast(dict[str, Any], value)
        for nested in mapping.values():
            parts.extend(_flatten_evidence(nested))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts = []
        sequence = cast(list[Any] | tuple[Any, ...] | set[Any], value)
        for nested in sequence:
            parts.extend(_flatten_evidence(nested))
        return parts
    return []


@lru_cache(maxsize=64)
def _read_candidate_document(path_text: str) -> str:
    path = Path(path_text)
    if not path.exists() or not path.is_file() or path.stat().st_size > 2_000_000:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _extract_skill_subject(question: str) -> str | None:
    normalized = _normalize_question(question)
    patterns = (
        r"(?:experience|knowledge|familiarity)\s+(?:with|in|of)\s+(.+)$",
        r"proficien(?:t|cy)\s+(?:with|in)\s+(.+)$",
        r"do you (?:have|possess)\s+(.+)$",
        r"(?:erfahrung|kenntnisse|vertrautheit)\s+(?:mit|in)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        subject = re.sub(
            r"\b(?:experience|knowledge|skills?|proficiency|familiarity|ja|nein)\b",
            " ",
            match.group(1),
        )
        subject = re.sub(r"\s+", " ", subject).strip()
        if len(subject) >= 3:
            return subject
    return None


def _try_positive_candidate_evidence(
    field: FormField,
    job: ApplicationJob,
) -> FieldMapping | None:
    """Answer a yes/no skill question only when candidate evidence says yes.

    Absence of a skill is never treated as "No". That would invent a fact;
    unresolved questions remain interventions instead.
    """
    if field.type not in {"radio", "select"} or not _has_yes_no_options(field):
        return None
    subject = _extract_skill_subject(_question_text(field))
    if subject is None:
        return None

    evidence_parts = _flatten_evidence(job.metadata.get("candidate_profile", {}))
    if job.documents and job.documents.cv_md:
        evidence_parts.append(_read_candidate_document(job.documents.cv_md))
    evidence = _normalize_question(" ".join(evidence_parts))
    if subject not in evidence:
        return None
    return FieldMapping(
        field_selector=field.selector,
        value="Yes",
        source="candidate_profile",
        confidence=0.85,
        requires_user_confirmation=False,
        explanation=f"Candidate profile or CV contains evidence for: {subject}",
    )


def _normalize_label(label: str) -> str:
    """Normalize a label for matching."""
    normalized = label.strip().lower()
    return re.sub(r"\s*(?:\*|\(required\)|required|mandatory)\s*$", "", normalized).strip()


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

    Only label and name are considered for high-precision mapping.
    Nearby/help text is intentionally ignored — generic help like
    'Anschreiben, Lebenslauf, Zeugnisse' near a label
    'Vollständige Bewerbungsunterlagen' must NOT auto-map to cv_pdf;
    such ambiguous fields become interventions for owner selection.
    """
    label = _normalize_label(field.label)
    name = _normalize_label(field.name)

    for text in (label, name):
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
    - PLZ/Street fields have no canonical CandidateProfile counterpart and
      must not be guessed from city/country.
    """
    # Never map password fields.
    if field.type == "unknown" and "password" in _normalize_label(field.label):
        return None

    # Explicit owner-confirmed per-job answers take precedence over
    # automatic inference guards. This allows PLZ/Straße to be filled when
    # the owner has explicitly provided the answer via intervention resolution.
    explicit_answer = _try_explicit_job_answer(field, job)
    if explicit_answer is not None:
        return explicit_answer

    # Guard: PLZ / Street have no CandidateProfile field — never invent.
    _addr_label = _normalize_label(field.label) + " " + _normalize_label(field.name)
    if re.search(r"plz|postleitzahl|strasse|straße|anschrift", _addr_label, re.IGNORECASE):
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

    if field.type == "textarea" and re.search(
        r"cover\s*letter|anschreiben",
        _question_text(field),
        re.IGNORECASE,
    ):
        cover_markdown = job.documents.cover_letter_md if job.documents else None
        if cover_markdown:
            raw_cover = _read_candidate_document(cover_markdown)
            if raw_cover:
                plain_cover = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw_cover)
                plain_cover = re.sub(r"[*_`#>]", "", plain_cover).strip()
                return FieldMapping(
                    field_selector=field.selector,
                    value=plain_cover,
                    source="application_job",
                    confidence=0.99,
                    requires_user_confirmation=False,
                    explanation="Mapped tailored cover-letter markdown to text field",
                )

    explicit_answer = _try_explicit_job_answer(field, job)
    if explicit_answer is not None:
        return explicit_answer

    positive_evidence = _try_positive_candidate_evidence(field, job)
    if positive_evidence is not None:
        return positive_evidence

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

    # Country locale alias: Germany <-> Deutschland, deterministic only
    # when label intent is confidently country and exactly one option is
    # unambiguous. No fuzzy/embedding, no general translation.
    if source_field == "country" and isinstance(value, str) and field.options:
        norm_val = _normalize_question(str(value))
        alias_targets: dict[str, list[str]] = {
            "germany": ["deutschland", "germany"],
            "deutschland": ["germany", "deutschland"],
        }
        targets = alias_targets.get(norm_val)
        if targets is not None:
            matching: list[FieldOption] = []
            for opt in field.options:
                nv = _normalize_question(opt.value or "")
                nl = _normalize_question(opt.label or "")
                if nv in targets or nl in targets:
                    matching.append(opt)
            if len(matching) == 1:
                chosen = matching[0].value or matching[0].label
                if not chosen:
                    return None
                return FieldMapping(
                    field_selector=field.selector,
                    value=chosen,
                    source=source_name,
                    confidence=0.95,
                    requires_user_confirmation=False,
                    explanation=explanation + " (country alias Germany<->Deutschland)",
                )
            if len(matching) != 1:
                return None

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
