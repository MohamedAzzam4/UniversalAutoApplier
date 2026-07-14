"""Answer validation — validate proposed answers before filling.

Per the llm-question-resolution workpackage, before a proposed answer is
filled into a form field, it must pass validation:

- For select/radio/checkbox: the answer must match an actual available
  option (after normalization).
- Character limits and field types are respected.
- Answers outside permitted options are rejected.
- Unsupported factual claims are rejected (the LLM must cite evidence).
- Contradictions with existing facts are detected.
- Confidence below the configured threshold requires confirmation.

This module is pure logic — it does not call the LLM or the browser.
"""

from __future__ import annotations

import re

from universal_auto_applier.core.question_models import (
    QuestionResolution,
)
from universal_auto_applier.llm.truth_ledger import CandidateTruthLedger


def _normalize(value: str) -> str:
    """Normalize a value for option matching: lowercase, alphanumeric + spaces."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


# Aliases for yes/no matching.
_YES_ALIASES = {"yes", "true", "1", "ja", "y"}
_NO_ALIASES = {"no", "false", "0", "nein", "n"}


def _normalize_yes_no(value: str) -> str:
    """Normalize a yes/no value. Returns 'yes', 'no', or the original normalized value."""
    normalized = _normalize(value)
    if normalized in _YES_ALIASES:
        return "yes"
    if normalized in _NO_ALIASES:
        return "no"
    return normalized


def match_option(
    proposed_value: str,
    options: list[str],
) -> str | None:
    """Match a proposed value to an available option.

    Args:
        proposed_value: The value proposed by the LLM or deterministic mapper.
        options: The available options (labels or values) from the form field.

    Returns:
        The matched option (original string from ``options``), or None if
        no match is found.

    Matching strategy (in priority order):
    1. Exact match (case-insensitive).
    2. Normalized match (alphanumeric only, case-insensitive).
    3. Yes/no alias match (for yes/no questions).
    4. Substring match (if the proposed value is contained in an option).
    """
    if not proposed_value or not options:
        return None

    proposed_lower = proposed_value.strip().lower()

    # 1. Exact match (case-insensitive).
    for option in options:
        if option.strip().lower() == proposed_lower:
            return option

    # 2. Normalized match.
    proposed_norm = _normalize(proposed_value)
    for option in options:
        if _normalize(option) == proposed_norm:
            return option

    # 3. Yes/no alias match.
    proposed_yn = _normalize_yes_no(proposed_value)
    for option in options:
        option_yn = _normalize_yes_no(option)
        if proposed_yn in ("yes", "no") and option_yn == proposed_yn:
            return option

    # 4. Substring match (proposed contained in option).
    for option in options:
        if proposed_norm and proposed_norm in _normalize(option):
            return option

    return None


def validate_answer(
    resolution: QuestionResolution,
    ledger: CandidateTruthLedger | None = None,
    max_chars: int = 5000,
) -> QuestionResolution:
    """Validate a proposed answer against the question's constraints.

    Args:
        resolution: The :class:`QuestionResolution` to validate.
        ledger: Optional truth ledger for contradiction detection.
        max_chars: Maximum character limit for text fields.

    Returns:
        The updated :class:`QuestionResolution`. If validation fails,
        ``proposed_answer`` is set to None and ``unresolved_reason`` or
        ``refusal`` is set. Never raises.
    """
    if not resolution.is_resolved:
        return resolution

    assert resolution.proposed_answer is not None  # for type narrowing
    candidate = resolution.proposed_answer
    question = resolution.question

    # 1. For select/radio/checkbox: must match an available option.
    if question.field_type in ("select", "radio", "checkbox") and question.options:
        matched = match_option(candidate.value, question.options)
        if matched is None:
            return QuestionResolution(
                question=question,
                category=resolution.category,
                risk_level=resolution.risk_level,
                proposed_answer=None,
                requires_human_confirmation=True,
                unresolved_reason=f"answer_not_in_options: {candidate.value!r} not in {question.options}",
            )
        # Update the normalized value to the matched option.
        candidate.normalized_value = matched

    # 2. Character limit for text/textarea fields.
    if question.field_type in ("text", "textarea") and len(candidate.value) > max_chars:
        return QuestionResolution(
            question=question,
            category=resolution.category,
            risk_level=resolution.risk_level,
            proposed_answer=None,
            requires_human_confirmation=True,
            unresolved_reason=f"answer_exceeds_char_limit: {len(candidate.value)} > {max_chars}",
        )

    # 3. Evidence requirement: LLM-grounded answers must cite at least
    #    one evidence fact. An empty evidence list means the answer was
    #    invented, which is forbidden.
    if candidate.source_type == "llm_grounded" and not candidate.evidence:
        return QuestionResolution(
            question=question,
            category=resolution.category,
            risk_level=resolution.risk_level,
            proposed_answer=None,
            requires_human_confirmation=True,
            refusal="no_evidence_cited",
        )

    # 4. Contradiction detection: if the ledger has an explicit fact for
    #    this question, the proposed answer must not contradict it.
    if ledger is not None:
        explicit = ledger.has_explicit_fact_for(question.question_text)
        if explicit is not None:
            # Extract the answer part from the explicit fact.
            explicit_answer = explicit.fact.split(":", 1)[-1].strip()
            if _normalize_yes_no(explicit_answer) in ("yes", "no"):
                proposed_yn = _normalize_yes_no(candidate.value)
                if proposed_yn in ("yes", "no") and proposed_yn != _normalize_yes_no(
                    explicit_answer
                ):
                    return QuestionResolution(
                        question=question,
                        category=resolution.category,
                        risk_level=resolution.risk_level,
                        proposed_answer=None,
                        requires_human_confirmation=True,
                        unresolved_reason=f"contradicts_explicit_answer: {candidate.value!r} vs {explicit_answer!r}",
                    )

    # 5. Confidence threshold: if confidence is below 0.0 (invalid),
    #    reject. (The auto-fill threshold is checked by the caller via
    #    ``requires_confirmation``.)
    if candidate.confidence < 0.0 or candidate.confidence > 1.0:
        return QuestionResolution(
            question=question,
            category=resolution.category,
            risk_level=resolution.risk_level,
            proposed_answer=None,
            requires_human_confirmation=True,
            unresolved_reason=f"invalid_confidence: {candidate.confidence}",
        )

    return resolution


__all__ = [
    "match_option",
    "validate_answer",
]
