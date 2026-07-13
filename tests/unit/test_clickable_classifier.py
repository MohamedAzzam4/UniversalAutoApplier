"""Tests for :mod:`universal_auto_applier.navigator.clickable_classifier`.

Covers English and German labels, dangerous submit blocking, and edge cases.
"""

from __future__ import annotations

import pytest

from universal_auto_applier.core.statuses import ClickableClassification
from universal_auto_applier.navigator.clickable_classifier import classify_clickable


class TestSafeApply:
    @pytest.mark.parametrize(
        "text",
        ["apply", "Apply", "APPLY", "Apply Now", "apply now", "Start Application"],
    )
    def test_english_safe_apply(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.SAFE_APPLY
        assert result.confidence > 0

    @pytest.mark.parametrize("text", ["bewerben", "Jetzt bewerben", "BEWERBEN"])
    def test_german_safe_apply(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.SAFE_APPLY

    def test_aria_label_matched(self) -> None:
        result = classify_clickable(text="", aria_label="Apply now", tag="button")
        assert result.classification == ClickableClassification.SAFE_APPLY


class TestSafeContinue:
    @pytest.mark.parametrize("text", ["next", "Next", "Continue", "continue", "Save and Continue"])
    def test_english_safe_continue(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.SAFE_CONTINUE

    @pytest.mark.parametrize("text", ["Weiter", "fortfahren", "Weitergehen"])
    def test_german_safe_continue(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.SAFE_CONTINUE


class TestSafeUpload:
    @pytest.mark.parametrize("text", ["Upload resume", "Upload CV", "Attach file"])
    def test_safe_upload(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.SAFE_UPLOAD

    def test_german_safe_upload(self) -> None:
        result = classify_clickable(text="Lebenslauf hochladen", tag="button")
        assert result.classification == ClickableClassification.SAFE_UPLOAD


class TestDangerousSubmit:
    @pytest.mark.parametrize(
        "text",
        [
            "submit",
            "Submit",
            "Submit application",
            "Send application",
            "Finish",
            "Complete application",
        ],
    )
    def test_english_dangerous_submit(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    @pytest.mark.parametrize("text", ["Absenden", "Bewerbung absenden", "Bewerbung senden"])
    def test_german_dangerous_submit(self, text: str) -> None:
        result = classify_clickable(text=text, tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    def test_submit_never_classified_as_safe(self) -> None:
        """The classifier must NEVER mark a submit button as safe."""
        result = classify_clickable(text="Submit application", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT
        assert result.classification not in (
            ClickableClassification.SAFE_APPLY,
            ClickableClassification.SAFE_CONTINUE,
            ClickableClassification.SAFE_UPLOAD,
        )

    def test_submit_in_aria_label(self) -> None:
        result = classify_clickable(text="Confirm", aria_label="Submit", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT


class TestLogin:
    @pytest.mark.parametrize("text", ["Login", "Log in", "Sign in", "Anmelden"])
    def test_login_classification(self, text: str) -> None:
        result = classify_clickable(text=text, tag="a")
        assert result.classification == ClickableClassification.LOGIN


class TestUnknown:
    def test_unknown_text(self) -> None:
        result = classify_clickable(text="Click here for more info", tag="a")
        assert result.classification == ClickableClassification.UNKNOWN

    def test_empty_text(self) -> None:
        result = classify_clickable(text="", tag="button")
        assert result.classification == ClickableClassification.UNKNOWN

    def test_random_text(self) -> None:
        result = classify_clickable(text="Lorem ipsum dolor sit amet", tag="div")
        assert result.classification == ClickableClassification.UNKNOWN


class TestDisabledAndInvisible:
    def test_disabled_is_unknown(self) -> None:
        result = classify_clickable(text="Apply now", tag="button", enabled=False)
        assert result.classification == ClickableClassification.UNKNOWN

    def test_invisible_is_unknown(self) -> None:
        result = classify_clickable(text="Apply now", tag="button", visible=False)
        assert result.classification == ClickableClassification.UNKNOWN


class TestPriorityAndSubstring:
    def test_submit_takes_priority_over_apply(self) -> None:
        """If both 'submit' and 'apply' appear, it's dangerous."""
        result = classify_clickable(text="Submit and apply", tag="button")
        assert result.classification == ClickableClassification.DANGEROUS_SUBMIT

    def test_login_takes_priority_over_apply(self) -> None:
        """If both 'login' and 'apply' appear, it's login."""
        result = classify_clickable(text="Sign in to apply", tag="a")
        assert result.classification == ClickableClassification.LOGIN

    def test_substring_match(self) -> None:
        """Substring matching works for compound labels."""
        result = classify_clickable(text="Click here to apply now", tag="button")
        assert result.classification == ClickableClassification.SAFE_APPLY
