"""Question classification — assign a category and risk to each question.

Per the llm-question-resolution workpackage, every form question is
classified into one of :class:`QuestionCategory` and assigned a
:class:`QuestionRisk` level. The category determines which safety rules
apply:

- ``LOW`` risk: deterministic candidate facts (name, email, phone).
  Safe to auto-fill via the existing deterministic mapper.
- ``MEDIUM`` risk: skills/experience and job-specific motivation.
  The LLM may answer with evidence; auto-fill above the confidence
  threshold.
- ``HIGH`` risk: work authorization, availability, salary, relocation,
  legal, demographic, consent/signature. ALWAYS requires human
  confirmation even if the LLM proposes an answer.

Classification is rule-based (regex on the question text) and
deterministic. It does NOT invoke the LLM.
"""

from __future__ import annotations

import re

from universal_auto_applier.core.question_models import (
    HIGH_RISK_CATEGORIES,
    ApplicationQuestion,
    QuestionCategory,
    QuestionRisk,
)


def _normalize(text: str) -> str:
    """Normalize text for classification: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower()).strip()


# Category patterns. Order matters: the first match wins.
# Patterns are matched against the normalized question text.
_CATEGORY_PATTERNS: list[tuple[QuestionCategory, list[str]]] = [
    # High-risk categories (checked first so they take priority).
    (
        QuestionCategory.LEGAL_DECLARATION,
        [
            r"criminal\s*record",
            r"background\s*check",
            r"vorstrafen",
            r"führungszeugnis",
            r"legal\s*declaration",
            r"have\s*you\s*ever\s*been\s*convicted",
            r"declaration\s*of\s*compliance",
            r"\bnda\b",
            r"non.?disclosure",
            r"confidentiality\s*agreement",
        ],
    ),
    (
        QuestionCategory.DEMOGRAPHIC_SENSITIVE,
        [
            r"\bgender\b",
            r"\bsex\b",
            r"\bethnic",
            r"\brace\b",
            r"disability",
            r"disabled",
            r"veteran",
            r"date\s*of\s*birth",
            r"\bdob\b",
            r"age\s*\(",
            r"religion",
            r"sexual\s*orientation",
            r"gender\s*identity",
            r"geschlecht",
            r"alter",
            r"geburtstag",
        ],
    ),
    (
        QuestionCategory.SALARY,
        [
            r"salary",
            r"compensation",
            r"expected\s*pay",
            r"hourly\s*rate",
            r"annual\s*salary",
            r"gehalt",
            r"vergütung",
            r"stundensatz",
        ],
    ),
    (
        QuestionCategory.RELOCATION,
        [
            r"relocat",
            r"move\s*to",
            r"willing\s*to\s*move",
            r"umzug",
            r"bereitschaft\s*zum\s*umzug",
        ],
    ),
    (
        QuestionCategory.WORK_AUTHORIZATION,
        [
            r"visa",
            r"sponsorship",
            r"work\s*authorization",
            r"authorized\s*to\s*work",
            r"require\s*sponsorship",
            r"work\s*permit",
            r"aufenthalts",
            r"arbeitserlaubnis",
            r"visum",
        ],
    ),
    (
        QuestionCategory.AVAILABILITY,
        [
            r"start\s*date",
            r"earliest\s*start",
            r"available\s*from",
            r"notice\s*period",
            r"hours\s*per\s*week",
            r"availability",
            r"einstermin",
            r"verfügbar",
            r"stunden\s*pro\s*woche",
        ],
    ),
    (
        QuestionCategory.CONSENT_SIGNATURE,
        [
            r"\bsign\s*\b",
            r"signature",
            r"e.?sign",
            r"i\s*agree",
            r"consent",
            r"accept\s*terms",
            r"accept\s*privacy",
            r"acknowledge",
            r"unterschrift",
            r"einwilligung",
            r"datenschutz",
        ],
    ),
    # Medium-risk categories.
    (
        QuestionCategory.JOB_SPECIFIC_MOTIVATION,
        [
            r"why\s*do\s*you\s*want",
            r"why\s*this\s*company",
            r"why\s*this\s*role",
            r"motivation",
            r"cover\s*letter",
            r"tell\s*us\s*about\s*yourself",
            r"why\s*should\s*we\s*hire",
            r"what\s*interests\s*you",
            r"motivationsschreiben",
            r"warum\s*dieses",
        ],
    ),
    (
        QuestionCategory.SKILLS_EXPERIENCE,
        [
            r"experience\s*with",
            r"knowledge\s*of",
            r"familiar\s*with",
            r"proficien",
            r"do\s*you\s*have\s*experience",
            r"how\s*many\s*years",
            r"skill",
            r"erfahrung\s*mit",
            r"kenntnisse",
            r"jahre\s*erfahrung",
        ],
    ),
    # Low-risk deterministic candidate facts.
    (
        QuestionCategory.DETERMINISTIC_CANDIDATE_FACT,
        [
            r"^first\s*name$",
            r"^last\s*name$",
            r"^full\s*name$",
            r"^name$",
            r"^email",
            r"^e.?mail",
            r"^phone",
            r"^mobile",
            r"^tel",
            r"linkedin",
            r"github",
            r"website",
            r"^url$",
            r"^city$",
            r"^country$",
            r"location",
            r"^street$",
            r"^address$",
            r"^zip$",
            r"^postal\s*code$",
        ],
    ),
]


def classify_question(question: ApplicationQuestion) -> tuple[QuestionCategory, QuestionRisk]:
    """Classify a question and assign a risk level.

    Args:
        question: The :class:`ApplicationQuestion` to classify.

    Returns:
        A tuple of (category, risk_level).

    Classification is rule-based and deterministic. It does NOT invoke
    the LLM. If no pattern matches, the question is classified as
    ``UNKNOWN_AMBIGUOUS`` with ``HIGH`` risk (requires human
    confirmation).
    """
    # Check the question text and nearby text.
    text = _normalize(f"{question.question_text} {question.nearby_text}")

    for category, patterns in _CATEGORY_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, text):
                risk = _risk_for_category(category)
                return category, risk

    # No match — unknown/ambiguous, high risk.
    return QuestionCategory.UNKNOWN_AMBIGUOUS, QuestionRisk.HIGH


def _risk_for_category(category: QuestionCategory) -> QuestionRisk:
    """Determine the risk level for a category.

    - HIGH risk: legal, salary, relocation, demographic, consent,
      work authorization, availability, unknown.
    - MEDIUM risk: skills/experience, job-specific motivation.
    - LOW risk: deterministic candidate facts.
    """
    if category in HIGH_RISK_CATEGORIES:
        return QuestionRisk.HIGH
    if category == QuestionCategory.UNKNOWN_AMBIGUOUS:
        return QuestionRisk.HIGH
    if category in (
        QuestionCategory.SKILLS_EXPERIENCE,
        QuestionCategory.JOB_SPECIFIC_MOTIVATION,
    ):
        return QuestionRisk.MEDIUM
    return QuestionRisk.LOW


def requires_confirmation(category: QuestionCategory, confidence: float) -> bool:
    """Determine whether a question requires human confirmation.

    Rules:
    - HIGH-risk categories ALWAYS require confirmation, regardless of
      confidence.
    - MEDIUM-risk categories require confirmation if confidence is
      below the auto-fill threshold (0.8 for LLM answers).
    - LOW-risk categories (deterministic) never require confirmation
      (the deterministic mapper already handles confidence).
    """
    risk = _risk_for_category(category)
    if risk == QuestionRisk.HIGH:
        return True
    if risk == QuestionRisk.MEDIUM:
        return confidence < 0.8
    return False


__all__ = [
    "classify_question",
    "requires_confirmation",
]
