"""Structured contracts for LLM-backed form-question resolution.

Per the llm-question-resolution workpackage, these contracts define the
typed data that flows through the question-resolution pipeline:

    FormField (existing)
        -> ApplicationQuestion (this module)
        -> QuestionClassification (this module)
        -> QuestionResolution (this module)
        -> FillResult / Intervention (existing)

Safety invariants enforced by these contracts:
- Every :class:`AnswerEvidence` has provenance (where the fact came from).
- :class:`QuestionResolution` always carries a ``risk_level`` and
  ``requires_human_confirmation`` flag. Risky categories (legal,
  salary, demographic, etc.) always require confirmation.
- :class:`QuestionResolution` never exposes hidden reasoning or
  chain-of-thought. Only a concise evidence-based ``explanation`` is
  stored.
- A ``refusal`` or ``unresolved_reason`` is set when the service cannot
  answer safely. The caller creates an intervention in that case.
- ``reusable_answer_eligible`` flags answers that the user has approved
  for reuse in future applications (stored in the truth ledger).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class QuestionCategory(StrEnum):
    """Classification categories for form questions.

    The category determines which safety rules apply:
    - ``DETERMINISTIC_CANDIDATE_FACT``: name, email, phone — mapped by
      the existing deterministic field mapper, no LLM needed.
    - ``SKILLS_EXPERIENCE``: "Do you have experience with X?" — the LLM
      may answer Yes only if evidence is found; absence of evidence is
      never treated as No.
    - ``JOB_SPECIFIC_MOTIVATION``: "Why do you want to work here?" —
      the LLM may compose an answer from the tailored cover letter.
    - ``WORK_AUTHORIZATION``: visa/sponsorship — requires explicit
      stored fact or human confirmation.
    - ``AVAILABILITY``: start date, hours — requires explicit stored
      fact or human confirmation.
    - ``SALARY``: salary expectations — requires explicit stored fact
      or human confirmation.
    - ``RELOCATION``: willingness to relocate — requires explicit
      stored fact or human confirmation.
    - ``LEGAL_DECLARATION``: criminal record, background check —
      requires explicit stored fact or human confirmation.
    - ``DEMOGRAPHIC_SENSITIVE``: gender, ethnicity, disability —
      requires explicit stored fact or human confirmation.
    - ``CONSENT_SIGNATURE``: consent checkboxes, e-signatures —
      requires explicit stored fact or human confirmation.
    - ``UNKNOWN_AMBIGUOUS``: cannot classify — requires human
      confirmation.
    """

    DETERMINISTIC_CANDIDATE_FACT = "deterministic_candidate_fact"
    SKILLS_EXPERIENCE = "skills_experience"
    JOB_SPECIFIC_MOTIVATION = "job_specific_motivation"
    WORK_AUTHORIZATION = "work_authorization"
    AVAILABILITY = "availability"
    SALARY = "salary"
    RELOCATION = "relocation"
    LEGAL_DECLARATION = "legal_declaration"
    DEMOGRAPHIC_SENSITIVE = "demographic_sensitive"
    CONSENT_SIGNATURE = "consent_signature"
    UNKNOWN_AMBIGUOUS = "unknown_ambiguous"


class QuestionRisk(StrEnum):
    """Risk level assigned to a question.

    - ``LOW``: deterministic candidate facts; safe to auto-fill.
    - ``MEDIUM``: skills/motivation; LLM may answer with evidence;
      auto-fill above confidence threshold.
    - ``HIGH``: legal, salary, relocation, demographic, consent;
      always requires human confirmation even if the LLM proposes an
      answer.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Categories that ALWAYS require human confirmation, regardless of
# confidence. The LLM may propose an answer, but the user must approve
# it before it is filled. This is a hard safety rule.
HIGH_RISK_CATEGORIES: frozenset[QuestionCategory] = frozenset(
    {
        QuestionCategory.WORK_AUTHORIZATION,
        QuestionCategory.AVAILABILITY,
        QuestionCategory.SALARY,
        QuestionCategory.RELOCATION,
        QuestionCategory.LEGAL_DECLARATION,
        QuestionCategory.DEMOGRAPHIC_SENSITIVE,
        QuestionCategory.CONSENT_SIGNATURE,
    }
)


class AnswerEvidence(BaseModel):
    """One piece of evidence supporting a proposed answer.

    Every fact must have provenance. The ``source`` field identifies
    where the fact came from so the user can audit it.
    """

    source: str = Field(
        ...,
        description="Provenance: 'candidate_profile', 'cv_markdown', 'cover_letter_markdown', 'job_description', 'answer_memory', 'application_metadata'.",
    )
    fact: str = Field(..., description="The factual statement extracted from the source.")
    source_ref: str = Field(
        default="",
        description="Optional reference into the source (e.g. line number, field name).",
    )


class AnswerCandidate(BaseModel):
    """A candidate answer proposed by the LLM or deterministic mapper.

    The ``value`` is the raw proposed answer. The ``normalized_value``
    is the answer after normalization (e.g. "Yes"/"No" for yes/no
    questions, or the matched option label for select/radio).
    """

    value: str = Field(..., description="The raw proposed answer.")
    normalized_value: str = Field(
        default="", description="The normalized answer (matched option, Yes/No, etc.)."
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the answer.")
    evidence: list[AnswerEvidence] = Field(
        default_factory=list[AnswerEvidence],
        description="Evidence supporting the answer. Empty list = no evidence (refusal).",
    )
    source_type: str = Field(
        ...,
        description="'deterministic_mapper', 'answer_memory', 'llm_grounded', or 'explicit_metadata'.",
    )
    explanation: str = Field(
        default="",
        description="Concise evidence-based explanation. No hidden reasoning or chain-of-thought.",
    )


class ApplicationQuestion(BaseModel):
    """A form question extracted from the page, normalized for resolution.

    This wraps a :class:`FormField` with additional context needed for
    LLM resolution: the question text, available options, and whether
    the field is required.
    """

    question_text: str = Field(..., description="The question label/text shown to the candidate.")
    field_selector: str = Field(..., description="CSS selector or token for the form field.")
    field_type: str = Field(..., description="FormField type: text, textarea, select, radio, etc.")
    options: list[str] = Field(
        default_factory=list[str],
        description="Available options for select/radio/checkbox fields.",
    )
    required: bool = Field(default=False, description="Whether the field is required.")
    nearby_text: str = Field(default="", description="Surrounding text that may provide context.")
    page_url: str = Field(default="", description="URL of the page where the question appears.")


class QuestionResolution(BaseModel):
    """The complete resolution of one form question.

    This is the output of the question-resolution pipeline. It carries
    the proposed answer (if any), the classification, the risk level,
    and whether human confirmation is required.

    If the question cannot be answered safely, ``refusal`` is set to a
    short reason and ``proposed_answer`` is None. The caller creates an
    intervention in that case.
    """

    question: ApplicationQuestion
    category: QuestionCategory
    risk_level: QuestionRisk
    proposed_answer: AnswerCandidate | None = Field(
        default=None,
        description="The proposed answer, or None if the question is refused/unresolved.",
    )
    requires_human_confirmation: bool = Field(
        default=False,
        description="True if the user must approve before the answer is filled.",
    )
    refusal: str = Field(
        default="",
        description="If set, the question was refused. The caller creates an intervention.",
    )
    unresolved_reason: str = Field(
        default="",
        description="If set, the question could not be resolved (e.g. no evidence, timeout).",
    )
    reusable_answer_eligible: bool = Field(
        default=False,
        description="True if the user has approved this answer for reuse in future applications.",
    )

    @property
    def is_resolved(self) -> bool:
        """True if a proposed answer exists and no refusal was issued."""
        return self.proposed_answer is not None and not self.refusal

    @property
    def can_auto_fill(self) -> bool:
        """True if the answer can be filled without human confirmation.

        This requires:
        1. A proposed answer exists.
        2. No refusal was issued.
        3. Human confirmation is not required (low/medium risk above
           confidence threshold).
        """
        return self.is_resolved and not self.requires_human_confirmation


__all__ = [
    "AnswerCandidate",
    "AnswerEvidence",
    "ApplicationQuestion",
    "HIGH_RISK_CATEGORIES",
    "QuestionCategory",
    "QuestionResolution",
    "QuestionRisk",
]
