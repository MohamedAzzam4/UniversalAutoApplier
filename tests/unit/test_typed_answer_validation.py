"""Unit tests for typed-answer validation in the live form executor.

These tests exercise :func:`validate_typed_answer` directly, without a
browser. They prove that:

- number fields reject "Yes" and accept "5".
- date fields reject free-form text and accept YYYY-MM-DD.
- checkbox fields reject arbitrary strings and accept yes/no.
- select/radio fields reject answers outside the option set.
- select/radio fields accept answers matching an available option.
- text/textarea/email/phone accept any non-empty string.

Per the form-modeling correction workpackage, invalid typed answers must
NEVER be classified as ``failed`` — they are routed to
``intervention_needed`` by the executor. A safely-unresolved question is
not a failure.
"""

from __future__ import annotations

from universal_auto_applier.core.models import FieldOption
from universal_auto_applier.form_engine.live_executor import validate_typed_answer

# ---------------------------------------------------------------------------
# number fields
# ---------------------------------------------------------------------------


class TestNumberValidation:
    def test_accepts_integer(self) -> None:
        ok, _ = validate_typed_answer("number", "5")
        assert ok

    def test_accepts_float(self) -> None:
        ok, _ = validate_typed_answer("number", "3.5")
        assert ok

    def test_accepts_zero(self) -> None:
        ok, _ = validate_typed_answer("number", "0")
        assert ok

    def test_rejects_yes(self) -> None:
        ok, reason = validate_typed_answer("number", "Yes")
        assert not ok
        assert "not a number" in reason

    def test_rejects_free_text(self) -> None:
        ok, reason = validate_typed_answer("number", "five years")
        assert not ok
        assert "not a number" in reason

    def test_rejects_empty(self) -> None:
        ok, _ = validate_typed_answer("number", "")
        assert not ok

    def test_rejects_none(self) -> None:
        ok, _ = validate_typed_answer("number", None)
        assert not ok


# ---------------------------------------------------------------------------
# date fields
# ---------------------------------------------------------------------------


class TestDateValidation:
    def test_accepts_iso_date(self) -> None:
        ok, _ = validate_typed_answer("date", "2025-01-15")
        assert ok

    def test_accepts_dotted_date(self) -> None:
        ok, _ = validate_typed_answer("date", "15.01.2025")
        assert ok

    def test_accepts_slashed_date(self) -> None:
        ok, _ = validate_typed_answer("date", "01/15/2025")
        assert ok

    def test_rejects_yes(self) -> None:
        ok, reason = validate_typed_answer("date", "Yes")
        assert not ok
        assert "not a date" in reason

    def test_rejects_free_text(self) -> None:
        ok, reason = validate_typed_answer("date", "next month")
        assert not ok
        assert "not a date" in reason

    def test_rejects_empty(self) -> None:
        ok, _ = validate_typed_answer("date", "")
        assert not ok


# ---------------------------------------------------------------------------
# checkbox fields
# ---------------------------------------------------------------------------


class TestCheckboxValidation:
    def test_accepts_yes(self) -> None:
        ok, _ = validate_typed_answer("checkbox", "Yes")
        assert ok

    def test_accepts_no(self) -> None:
        ok, _ = validate_typed_answer("checkbox", "No")
        assert ok

    def test_accepts_true(self) -> None:
        ok, _ = validate_typed_answer("checkbox", "true")
        assert ok

    def test_accepts_false(self) -> None:
        ok, _ = validate_typed_answer("checkbox", "false")
        assert ok

    def test_rejects_arbitrary_text(self) -> None:
        ok, reason = validate_typed_answer("checkbox", "maybe")
        assert not ok
        assert "yes/no" in reason

    def test_rejects_number(self) -> None:
        ok, _ = validate_typed_answer("checkbox", "5")
        assert not ok


# ---------------------------------------------------------------------------
# select fields
# ---------------------------------------------------------------------------


class TestSelectValidation:
    def _options(self) -> list[FieldOption]:
        return [
            FieldOption(value="de", label="Germany"),
            FieldOption(value="us", label="United States"),
        ]

    def test_accepts_matching_value(self) -> None:
        ok, _ = validate_typed_answer("select", "de", self._options())
        assert ok

    def test_accepts_matching_label(self) -> None:
        ok, _ = validate_typed_answer("select", "Germany", self._options())
        assert ok

    def test_accepts_case_insensitive(self) -> None:
        ok, _ = validate_typed_answer("select", "germany", self._options())
        assert ok

    def test_rejects_value_outside_options(self) -> None:
        ok, reason = validate_typed_answer("select", "france", self._options())
        assert not ok
        assert "not in options" in reason

    def test_rejects_yes_for_country_select(self) -> None:
        ok, reason = validate_typed_answer("select", "Yes", self._options())
        assert not ok
        assert "not in options" in reason

    def test_accepts_any_when_no_options_known(self) -> None:
        # When the executor has no option list (rare extraction failure),
        # accept and let Playwright raise if the fill fails. This avoids
        # false-positive interventions from incomplete metadata.
        ok, _ = validate_typed_answer("select", "anything", [])
        assert ok


# ---------------------------------------------------------------------------
# radio fields
# ---------------------------------------------------------------------------


class TestRadioValidation:
    def _options(self) -> list[FieldOption]:
        return [
            FieldOption(value="Yes", label="Yes"),
            FieldOption(value="No", label="No"),
        ]

    def test_accepts_yes(self) -> None:
        ok, _ = validate_typed_answer("radio", "Yes", self._options())
        assert ok

    def test_accepts_no(self) -> None:
        ok, _ = validate_typed_answer("radio", "No", self._options())
        assert ok

    def test_accepts_lowercase_yes(self) -> None:
        ok, _ = validate_typed_answer("radio", "yes", self._options())
        assert ok

    def test_rejects_maybe(self) -> None:
        ok, reason = validate_typed_answer("radio", "Maybe", self._options())
        assert not ok
        assert "not in options" in reason

    def test_rejects_number_for_radio(self) -> None:
        ok, reason = validate_typed_answer("radio", "5", self._options())
        assert not ok
        assert "not in options" in reason

    def test_valid_radio_answer_matches_one_available_option(self) -> None:
        """Required regression: a valid radio answer matching one available
        option must pass validation. This is the inverse of the
        'answer-not-in-options' rejection path."""
        ok, reason = validate_typed_answer("radio", "No", self._options())
        assert ok, f"Valid radio answer was rejected: {reason}"


# ---------------------------------------------------------------------------
# text/textarea/email/phone: any non-empty string accepted
# ---------------------------------------------------------------------------


class TestFreeTextValidation:
    def test_text_accepts_any_string(self) -> None:
        ok, _ = validate_typed_answer("text", "anything goes")
        assert ok

    def test_textarea_accepts_any_string(self) -> None:
        ok, _ = validate_typed_answer("textarea", "a long cover letter...")
        assert ok

    def test_email_accepts_any_string(self) -> None:
        ok, _ = validate_typed_answer("email", "user@example.com")
        assert ok

    def test_phone_accepts_any_string(self) -> None:
        ok, _ = validate_typed_answer("phone", "+49 1234567")
        assert ok

    def test_text_rejects_empty(self) -> None:
        ok, _ = validate_typed_answer("text", "")
        assert not ok

    def test_text_rejects_none(self) -> None:
        ok, _ = validate_typed_answer("text", None)
        assert not ok


# ---------------------------------------------------------------------------
# LiveFieldRecord carries options / selected_value / filled_value
# ---------------------------------------------------------------------------


class TestLiveFieldRecordNewFields:
    def test_record_carries_options(self) -> None:
        from universal_auto_applier.browser.live_models import LiveFieldRecord

        record = LiveFieldRecord(
            page_url="https://example.com",
            selector="input[name='k8s']",
            label="Do you have experience with Kubernetes?",
            field_type="radio",
            status="filled",
            field_token="live-field-0-3",
            options=["Yes", "No"],
            selected_value="Yes",
            filled_value="Yes",
        )
        assert record.options == ["Yes", "No"]
        assert record.selected_value == "Yes"
        assert record.filled_value == "Yes"

    def test_record_defaults_are_empty(self) -> None:
        from universal_auto_applier.browser.live_models import LiveFieldRecord

        record = LiveFieldRecord(
            page_url="https://example.com",
            selector="input[name='q']",
            label="Question?",
            field_type="text",
            status="skipped",
        )
        assert record.options == []
        assert record.selected_value == ""
        assert record.filled_value == ""
