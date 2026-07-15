"""Question resolver — the orchestrator for LLM-backed question resolution.

Per the llm-question-resolution workpackage, this module implements the
required sequence:

    deterministic mapping
    → retrieve evidence (truth ledger)
    → classify risk
    → request structured LLM answer when allowed
    → validate answer
    → fill or create intervention

The resolver is the single entry point for the live browser executor.
It decides whether a question can be answered deterministically, by the
LLM, or needs human intervention.

Safety:
- Deterministic mapping is tried first (existing ``field_mapper``).
- HIGH-risk categories always require human confirmation, even if the
  LLM proposes an answer.
- The LLM is only invoked for MEDIUM-risk categories (skills,
  motivation) and only if deterministic mapping failed.
- Absence of evidence is never treated as "No".
- Final submission is never triggered (the resolver only fills or
  creates interventions; it never clicks submit).
"""

from __future__ import annotations

import logging

from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldMapping,
    FormField,
)
from universal_auto_applier.core.question_models import (
    AnswerCandidate,
    AnswerEvidence,
    ApplicationQuestion,
    QuestionCategory,
    QuestionResolution,
    QuestionRisk,
)
from universal_auto_applier.form_engine.field_mapper import map_field
from universal_auto_applier.llm.answer_validator import validate_answer
from universal_auto_applier.llm.qa_service import (
    QuestionAnsweringService,
    create_qa_service,
)
from universal_auto_applier.llm.question_classifier import classify_question
from universal_auto_applier.llm.truth_ledger import (
    CandidateTruthLedger,
    Fact,
    build_ledger,
)

logger = logging.getLogger("universal_auto_applier.llm.question_resolver")


def _field_to_question(field: FormField) -> ApplicationQuestion:
    """Convert a :class:`FormField` to an :class:`ApplicationQuestion`."""
    options: list[str] = []
    for option in field.options:
        # Prefer the label, fall back to value.
        options.append(option.label or option.value)
    return ApplicationQuestion(
        question_text=field.label or field.nearby_text or field.name,
        field_selector=field.selector,
        field_type=field.type,
        options=options,
        required=field.required,
        nearby_text=field.nearby_text,
    )


def _mapping_to_resolution(
    mapping: FieldMapping,
    question: ApplicationQuestion,
    category: QuestionCategory,
    risk: QuestionRisk,
) -> QuestionResolution:
    """Convert a deterministic :class:`FieldMapping` to a :class:`QuestionResolution`."""
    evidence = [
        AnswerEvidence(
            source=mapping.source or "candidate_profile",
            fact=mapping.explanation,
        )
    ]
    candidate = AnswerCandidate(
        value=mapping.value,
        normalized_value=mapping.value,
        confidence=mapping.confidence,
        evidence=evidence,
        source_type="deterministic_mapper",
        explanation=mapping.explanation,
    )
    # Deterministic mappings don't require confirmation unless confidence
    # is low (the fill engine handles that separately).
    needs_confirmation = mapping.requires_user_confirmation
    return QuestionResolution(
        question=question,
        category=category,
        risk_level=risk,
        proposed_answer=candidate,
        requires_human_confirmation=needs_confirmation,
        reusable_answer_eligible=not needs_confirmation,
    )


def _refusal_resolution(
    question: ApplicationQuestion,
    category: QuestionCategory,
    risk: QuestionRisk,
    reason: str,
) -> QuestionResolution:
    """Build a refusal resolution (no proposed answer, needs confirmation)."""
    return QuestionResolution(
        question=question,
        category=category,
        risk_level=risk,
        proposed_answer=None,
        requires_human_confirmation=True,
        refusal=reason,
    )


def resolve_question(
    field: FormField,
    candidate: CandidateProfile,
    job: ApplicationJob,
    qa_service: QuestionAnsweringService | None = None,
    ledger: CandidateTruthLedger | None = None,
    answer_memory_facts: list[Fact] | None = None,
) -> QuestionResolution:
    """Resolve a single form question.

    This is the main entry point. It implements the required sequence:

    1. Convert the :class:`FormField` to an :class:`ApplicationQuestion`.
    2. Classify the question (category + risk).
    3. Try deterministic mapping (existing ``field_mapper``).
    4. If deterministic mapping fails and the category is MEDIUM risk,
       try the LLM (grounded by the truth ledger).
    5. Validate the answer (option matching, evidence, contradictions).
    6. Return the :class:`QuestionResolution`.

    HIGH-risk categories are never auto-filled by the LLM. The LLM may
    propose an answer, but ``requires_human_confirmation`` is always
    True. The caller creates an intervention.

    Args:
        field: The form field to resolve.
        candidate: The resolved candidate profile.
        job: The application job.
        qa_service: Optional :class:`QuestionAnsweringService`. If None,
            a default is created via :func:`create_qa_service`.
        ledger: Optional pre-built truth ledger. If None, one is built.
        answer_memory_facts: Optional reusable approved answers to
            include in the ledger.

    Returns:
        A :class:`QuestionResolution`. Never raises.
    """
    question = _field_to_question(field)
    category, risk = classify_question(question)

    # Build the truth ledger if not provided.
    if ledger is None:
        ledger = build_ledger(job, candidate, answer_memory_facts)

    # Step 1: Try deterministic mapping first.
    mapping = map_field(field, candidate, job)
    if mapping is not None:
        resolution = _mapping_to_resolution(mapping, question, category, risk)
        # Validate the deterministic answer too (e.g. option matching).
        resolution = validate_answer(resolution, ledger)
        if resolution.is_resolved:
            return resolution
        # If validation failed, fall through to LLM (if allowed).

    # Step 2: Check for an explicit answer in the ledger (answer memory
    # or application metadata). This was already tried by the
    # deterministic mapper, but we double-check here in case the mapper
    # missed it.
    explicit = ledger.has_explicit_fact_for(question.question_text)
    if explicit is not None:
        # Extract the answer from the explicit fact.
        answer_part = explicit.fact.split(":", 1)[-1].strip()
        candidate_answer = AnswerCandidate(
            value=answer_part,
            normalized_value=answer_part,
            confidence=1.0,
            evidence=[
                AnswerEvidence(
                    source=explicit.source,
                    fact=explicit.fact,
                    source_ref=explicit.source_ref,
                )
            ],
            source_type="explicit_metadata"
            if explicit.source == "application_metadata"
            else "answer_memory",
            explanation=f"Explicit user-provided answer from {explicit.source}",
        )
        resolution = QuestionResolution(
            question=question,
            category=category,
            risk_level=risk,
            proposed_answer=candidate_answer,
            requires_human_confirmation=False,  # Explicit user answers don't need confirmation.
            reusable_answer_eligible=True,
        )
        resolution = validate_answer(resolution, ledger)
        if resolution.is_resolved:
            return resolution

    # Step 3: For HIGH-risk categories, do NOT invoke the LLM. Return a
    # refusal that requires human confirmation.
    if risk == QuestionRisk.HIGH:
        return _refusal_resolution(
            question,
            category,
            risk,
            reason=f"high_risk_category_requires_confirmation: {category.value}",
        )

    # Step 4: For MEDIUM-risk categories, invoke the LLM (if configured).
    # For LOW-risk categories, deterministic mapping should have worked;
    # if it didn't, the LLM won't help (those are factual fields).
    if risk == QuestionRisk.LOW:
        return _refusal_resolution(
            question,
            category,
            risk,
            reason="deterministic_mapping_failed",
        )

    # MEDIUM risk — invoke the LLM.
    service = qa_service or create_qa_service()
    resolution = service.answer_question(question, category, ledger)

    # Step 5: Validate the LLM answer.
    resolution = validate_answer(resolution, ledger)

    return resolution


def resolve_questions(
    fields: list[FormField],
    candidate: CandidateProfile,
    job: ApplicationJob,
    qa_service: QuestionAnsweringService | None = None,
    answer_memory_facts: list[Fact] | None = None,
) -> list[QuestionResolution]:
    """Resolve multiple form questions.

    Args:
        fields: The form fields to resolve.
        candidate: The resolved candidate profile.
        job: The application job.
        qa_service: Optional :class:`QuestionAnsweringService`.
        answer_memory_facts: Optional reusable approved answers.

    Returns:
        A list of :class:`QuestionResolution`, one per field.
    """
    # Build the ledger once for all questions.
    ledger = build_ledger(job, candidate, answer_memory_facts)
    service = qa_service or create_qa_service()

    resolutions: list[QuestionResolution] = []
    for field in fields:
        resolution = resolve_question(
            field,
            candidate,
            job,
            qa_service=service,
            ledger=ledger,
        )
        resolutions.append(resolution)
    return resolutions


__all__ = [
    "resolve_question",
    "resolve_questions",
]
