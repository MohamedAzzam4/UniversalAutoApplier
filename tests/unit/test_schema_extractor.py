"""Tests for :mod:`universal_auto_applier.form_engine.schema_extractor`.

Fixture-based tests for form field extraction.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.form_engine.schema_extractor import extract_form_fields

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestTextFields:
    def test_extract_text_input(self) -> None:
        html = '<form><label for="fn">First name</label><input type="text" id="fn" name="first_name" required></form>'
        fields = extract_form_fields(html)

        assert len(fields) == 1
        assert fields[0].type == "text"
        assert fields[0].label == "First name"
        assert fields[0].required is True
        assert fields[0].name == "first_name"

    def test_extract_email_input(self) -> None:
        html = '<form><label for="em">Email address</label><input type="email" id="em" name="email" required></form>'
        fields = extract_form_fields(html)

        assert fields[0].type == "email"
        assert fields[0].label == "Email address"

    def test_extract_phone_input(self) -> None:
        html = '<form><label for="ph">Phone number</label><input type="tel" id="ph" name="phone"></form>'
        fields = extract_form_fields(html)

        assert fields[0].type == "phone"
        assert fields[0].label == "Phone number"

    def test_extract_textarea(self) -> None:
        html = '<form><label for="cl">Cover letter</label><textarea id="cl" name="cover_letter" required></textarea></form>'
        fields = extract_form_fields(html)

        assert fields[0].type == "textarea"
        assert fields[0].label == "Cover letter"

    def test_label_from_placeholder(self) -> None:
        html = '<form><input type="text" id="fn" name="first_name" placeholder="Enter your first name"></form>'
        fields = extract_form_fields(html)

        assert fields[0].label == "Enter your first name"

    def test_label_from_aria_label(self) -> None:
        html = '<form><input type="email" id="em" name="email" aria-label="Email address"></form>'
        fields = extract_form_fields(html)

        assert fields[0].label == "Email address"

    def test_label_from_name(self) -> None:
        html = '<form><input type="text" id="fn" name="first_name"></form>'
        fields = extract_form_fields(html)

        assert "First Name" in fields[0].label or "first" in fields[0].label.lower()


class TestSelectFields:
    def test_extract_select_with_options(self) -> None:
        html = _read_fixture("select_dropdown.html")
        fields = extract_form_fields(html)

        select_fields = [f for f in fields if f.type == "select"]
        assert len(select_fields) == 2
        assert len(select_fields[0].options) == 4  # including empty
        assert select_fields[0].options[1].value == "de"
        assert select_fields[0].options[1].label == "Germany"


class TestRadioCheckbox:
    def test_extract_radio_group(self) -> None:
        html = _read_fixture("radio_checkbox.html")
        fields = extract_form_fields(html)

        radio_fields = [f for f in fields if f.type == "radio"]
        assert len(radio_fields) == 1
        assert len(radio_fields[0].options) == 2
        assert radio_fields[0].options[0].value == "yes"
        assert radio_fields[0].options[1].value == "no"

    def test_extract_checkbox_group(self) -> None:
        html = _read_fixture("radio_checkbox.html")
        fields = extract_form_fields(html)

        checkbox_fields = [f for f in fields if f.type == "checkbox"]
        assert len(checkbox_fields) == 1
        assert len(checkbox_fields[0].options) == 3


class TestFileFields:
    def test_extract_file_input(self) -> None:
        html = '<form><label for="resume">Upload resume</label><input type="file" id="resume" name="resume" accept=".pdf"></form>'
        fields = extract_form_fields(html)

        assert fields[0].type == "file"
        assert fields[0].label == "Upload resume"


class TestPasswordField:
    def test_password_field_type_is_unknown(self) -> None:
        html = '<form><label for="pw">Password</label><input type="password" id="pw" name="password" required></form>'
        fields = extract_form_fields(html)

        # Password fields are mapped to type "unknown" for safety.
        assert fields[0].type == "unknown"


class TestSubmitButtonNotExtracted:
    def test_submit_button_not_in_fields(self) -> None:
        html = '<form><input type="text" name="name"><button type="submit">Submit</button></form>'
        fields = extract_form_fields(html)

        # Only the text input should be extracted, not the submit button.
        assert len(fields) == 1
        assert fields[0].type == "text"


class TestFullApplicationForm:
    def test_extract_all_fields(self) -> None:
        html = _read_fixture("full_application.html")
        fields = extract_form_fields(html)

        # Should have: first_name, last_name, email, phone, linkedin,
        # textarea, resume (file), cover_pdf (file) = 8 fields
        assert len(fields) == 8

        types = [f.type for f in fields]
        assert "text" in types
        assert "email" in types
        assert "phone" in types
        assert "textarea" in types
        assert "file" in types

    def test_labels_found_from_label_for(self) -> None:
        html = _read_fixture("full_application.html")
        fields = extract_form_fields(html)

        email_field = [f for f in fields if f.type == "email"][0]
        assert "email" in email_field.label.lower()
