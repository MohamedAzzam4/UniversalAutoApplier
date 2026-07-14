"""Unit tests for question classification.

Per the llm-question-resolution workpackage, tests cover:
- Each category is correctly classified.
- HIGH-risk categories always require confirmation.
- MEDIUM-risk categories require confirmation below threshold.
- LOW-risk categories never require confirmation.
- Unknown questions are classified as HIGH-risk.
"""

from __future__ import annotations

import pytest

from universal_auto_applier.core.question_models import (
    ApplicationQuestion,
    QuestionCategory,
    QuestionRisk,
)
from universal_auto_applier.llm.question_classifier import (
    classify_question,
    requires_confirmation,
)


def _make_question(text: str, **kwargs) -> ApplicationQuestion:
    return ApplicationQuestion(
        question_text=text,
        field_selector="test-selector",
        field_type=kwargs.get("field_type", "text"),
        options=kwargs.get("options", []),
        required=kwargs.get("required", False),
        nearby_text=kwargs.get("nearby_text", ""),
    )


class TestClassification:
    @pytest.mark.parametrize(
        "text,expected_category",
        [
            # Deterministic candidate facts (LOW risk).
            ("First name", QuestionCategory.DETERMINISTIC_CANDIDATE_FACT),
            ("Last name", QuestionCategory.DETERMINISTIC_CANDIDATE_FACT),
            ("Email address", QuestionCategory.DETERMINISTIC_CANDIDATE_FACT),
            ("Phone number", QuestionCategory.DETERMINISTIC_CANDIDATE_FACT),
            # Skills/experience (MEDIUM risk).
            ("Do you have experience with Python?", QuestionCategory.SKILLS_EXPERIENCE),
            ("Knowledge of SQL", QuestionCategory.SKILLS_EXPERIENCE),
            ("How many years of experience?", QuestionCategory.SKILLS_EXPERIENCE),
            # Job-specific motivation (MEDIUM risk).
            ("Why do you want to work here?", QuestionCategory.JOB_SPECIFIC_MOTIVATION),
            ("Why this company?", QuestionCategory.JOB_SPECIFIC_MOTIVATION),
            # Work authorization (HIGH risk).
            ("Do you require visa sponsorship?", QuestionCategory.WORK_AUTHORIZATION),
            ("Are you authorized to work in the EU?", QuestionCategory.WORK_AUTHORIZATION),
            # Availability (HIGH risk).
            ("What is your earliest start date?", QuestionCategory.AVAILABILITY),
            ("How many hours per week?", QuestionCategory.AVAILABILITY),
            # Salary (HIGH risk).
            ("What is your salary expectation?", QuestionCategory.SALARY),
            ("Expected compensation", QuestionCategory.SALARY),
            # Relocation (HIGH risk).
            ("Are you willing to relocate?", QuestionCategory.RELOCATION),
            # Legal declarations (HIGH risk).
            ("Have you ever been convicted of a crime?", QuestionCategory.LEGAL_DECLARATION),
            ("Criminal record check", QuestionCategory.LEGAL_DECLARATION),
            # Demographic/sensitive (HIGH risk).
            ("What is your gender?", QuestionCategory.DEMOGRAPHIC_SENSITIVE),
            ("Date of birth", QuestionCategory.DEMOGRAPHIC_SENSITIVE),
            ("What is your ethnicity?", QuestionCategory.DEMOGRAPHIC_SENSITIVE),
            # Consent/signature (HIGH risk).
            ("Please sign here", QuestionCategory.CONSENT_SIGNATURE),
            ("I agree to the terms", QuestionCategory.CONSENT_SIGNATURE),
            ("Do you consent to data processing?", QuestionCategory.CONSENT_SIGNATURE),
        ],
    )
    def test_classification(self, text: str, expected_category: QuestionCategory) -> None:
        question = _make_question(text)
        category, risk = classify_question(question)
        assert category == expected_category, (
            f"Expected {expected_category}, got {category} for {text!r}"
        )

    def test_unknown_question_is_high_risk(self) -> None:
        question = _make_question("What is your favorite color?")
        category, risk = classify_question(question)
        assert category == QuestionCategory.UNKNOWN_AMBIGUOUS
        assert risk == QuestionRisk.HIGH

    def test_german_question_classified(self) -> None:
        question = _make_question("Haben Sie Erfahrung mit Python?")
        category, risk = classify_question(question)
        assert category == QuestionCategory.SKILLS_EXPERIENCE
        assert risk == QuestionRisk.MEDIUM

    def test_nearby_text_used_for_classification(self) -> None:
        """Classification should consider nearby text, not just the label."""
        question = _make_question(
            "Select one",
            nearby_text="What is your gender?",
        )
        category, risk = classify_question(question)
        assert category == QuestionCategory.DEMOGRAPHIC_SENSITIVE
        assert risk == QuestionRisk.HIGH


class TestRiskLevels:
    @pytest.mark.parametrize(
        "category,expected_risk",
        [
            (QuestionCategory.DETERMINISTIC_CANDIDATE_FACT, QuestionRisk.LOW),
            (QuestionCategory.SKILLS_EXPERIENCE, QuestionRisk.MEDIUM),
            (QuestionCategory.JOB_SPECIFIC_MOTIVATION, QuestionRisk.MEDIUM),
            (QuestionCategory.WORK_AUTHORIZATION, QuestionRisk.HIGH),
            (QuestionCategory.AVAILABILITY, QuestionRisk.HIGH),
            (QuestionCategory.SALARY, QuestionRisk.HIGH),
            (QuestionCategory.RELOCATION, QuestionRisk.HIGH),
            (QuestionCategory.LEGAL_DECLARATION, QuestionRisk.HIGH),
            (QuestionCategory.DEMOGRAPHIC_SENSITIVE, QuestionRisk.HIGH),
            (QuestionCategory.CONSENT_SIGNATURE, QuestionRisk.HIGH),
            (QuestionCategory.UNKNOWN_AMBIGUOUS, QuestionRisk.HIGH),
        ],
    )
    def test_risk_for_category(
        self, category: QuestionCategory, expected_risk: QuestionRisk
    ) -> None:
        """Each category maps to the correct risk level."""
        # Build a question that matches the category.
        text_map = {
            QuestionCategory.DETERMINISTIC_CANDIDATE_FACT: "First name",
            QuestionCategory.SKILLS_EXPERIENCE: "Experience with Python?",
            QuestionCategory.JOB_SPECIFIC_MOTIVATION: "Why this company?",
            QuestionCategory.WORK_AUTHORIZATION: "Visa sponsorship?",
            QuestionCategory.AVAILABILITY: "Start date?",
            QuestionCategory.SALARY: "Salary expectation?",
            QuestionCategory.RELOCATION: "Willing to relocate?",
            QuestionCategory.LEGAL_DECLARATION: "Criminal record?",
            QuestionCategory.DEMOGRAPHIC_SENSITIVE: "Gender?",
            QuestionCategory.CONSENT_SIGNATURE: "Signature",
            QuestionCategory.UNKNOWN_AMBIGUOUS: "Favorite color?",
        }
        question = _make_question(text_map[category])
        _, risk = classify_question(question)
        assert risk == expected_risk


class TestRequiresConfirmation:
    def test_high_risk_always_requires_confirmation(self) -> None:
        """HIGH-risk categories always require confirmation, regardless of confidence."""
        assert requires_confirmation(QuestionCategory.SALARY, 0.99) is True
        assert requires_confirmation(QuestionCategory.LEGAL_DECLARATION, 0.99) is True
        assert requires_confirmation(QuestionCategory.DEMOGRAPHIC_SENSITIVE, 0.99) is True
        assert requires_confirmation(QuestionCategory.CONSENT_SIGNATURE, 0.99) is True
        assert requires_confirmation(QuestionCategory.WORK_AUTHORIZATION, 0.99) is True
        assert requires_confirmation(QuestionCategory.AVAILABILITY, 0.99) is True
        assert requires_confirmation(QuestionCategory.RELOCATION, 0.99) is True
        assert requires_confirmation(QuestionCategory.UNKNOWN_AMBIGUOUS, 0.99) is True

    def test_medium_risk_requires_confirmation_below_threshold(self) -> None:
        """MEDIUM-risk categories require confirmation below 0.8 confidence."""
        assert requires_confirmation(QuestionCategory.SKILLS_EXPERIENCE, 0.79) is True
        assert requires_confirmation(QuestionCategory.JOB_SPECIFIC_MOTIVATION, 0.79) is True

    def test_medium_risk_no_confirmation_above_threshold(self) -> None:
        """MEDIUM-risk categories do NOT require confirmation at/above 0.8 confidence."""
        assert requires_confirmation(QuestionCategory.SKILLS_EXPERIENCE, 0.80) is False
        assert requires_confirmation(QuestionCategory.SKILLS_EXPERIENCE, 0.95) is False
        assert requires_confirmation(QuestionCategory.JOB_SPECIFIC_MOTIVATION, 0.90) is False

    def test_low_risk_never_requires_confirmation(self) -> None:
        """LOW-risk categories never require confirmation."""
        assert requires_confirmation(QuestionCategory.DETERMINISTIC_CANDIDATE_FACT, 0.5) is False
        assert requires_confirmation(QuestionCategory.DETERMINISTIC_CANDIDATE_FACT, 0.0) is False
