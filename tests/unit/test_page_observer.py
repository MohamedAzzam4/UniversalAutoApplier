"""Tests for :mod:`universal_auto_applier.navigator.page_observer`.

Uses saved HTML fixtures — no browser is launched.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.core.statuses import (
    ClickableClassification,
    PageState,
)
from universal_auto_applier.navigator.page_observer import observe_html

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestApplyButtonPage:
    def test_detects_apply_button(self) -> None:
        html = _read_fixture("apply_button.html")
        obs = observe_html(html, url="https://example.com/jobs/123")

        apply_clickables = [
            c for c in obs.clickables if c.classification == ClickableClassification.SAFE_APPLY
        ]
        assert len(apply_clickables) == 1
        assert "apply" in apply_clickables[0].text.lower()

    def test_detects_job_page_state(self) -> None:
        html = _read_fixture("apply_button.html")
        obs = observe_html(html, url="https://example.com/jobs/123")
        # The page has "job description" and "responsibilities" text.
        assert obs.page_state == PageState.APPLY_PAGE

    def test_extracts_title(self) -> None:
        html = _read_fixture("apply_button.html")
        obs = observe_html(html)
        assert obs.title == "Apply Now Page"


class TestSimpleFormPage:
    def test_detects_form_state(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html, url="https://example.com/apply")

        assert obs.page_state == PageState.FORM

    def test_extracts_text_inputs(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html)

        input_names = [i.name for i in obs.inputs]
        assert "first_name" in input_names
        assert "last_name" in input_names
        assert "email" in input_names
        assert "phone" in input_names

    def test_extracts_form(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html)

        assert len(obs.forms) == 1
        assert obs.forms[0].action == "/submit"
        assert obs.forms[0].method == "post"

    def test_detects_dangerous_submit(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html)

        submit_clickables = [
            c
            for c in obs.clickables
            if c.classification == ClickableClassification.DANGEROUS_SUBMIT
        ]
        assert len(submit_clickables) == 1
        assert "submit" in submit_clickables[0].text.lower()

    def test_detects_safe_continue(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html)

        continue_clickables = [
            c for c in obs.clickables if c.classification == ClickableClassification.SAFE_CONTINUE
        ]
        assert len(continue_clickables) == 1
        assert "next" in continue_clickables[0].text.lower()

    def test_warnings_include_submit(self) -> None:
        html = _read_fixture("simple_application.html")
        obs = observe_html(html)

        assert any("dangerous_submit" in w for w in obs.warnings)


class TestFileUploadPage:
    def test_extracts_file_inputs(self) -> None:
        html = _read_fixture("file_upload.html")
        obs = observe_html(html)

        assert len(obs.file_inputs) == 2
        assert obs.file_inputs[0].name == "resume"
        assert obs.file_inputs[1].name == "cover_letter"

    def test_file_input_accept_attribute(self) -> None:
        html = _read_fixture("file_upload.html")
        obs = observe_html(html)

        assert ".pdf" in obs.file_inputs[0].accept

    def test_detects_upload_button(self) -> None:
        html = _read_fixture("file_upload.html")
        obs = observe_html(html)

        upload_clickables = [
            c for c in obs.clickables if c.classification == ClickableClassification.SAFE_UPLOAD
        ]
        assert len(upload_clickables) == 1


class TestLoginPage:
    def test_detects_login_state(self) -> None:
        html = _read_fixture("login_page.html")
        obs = observe_html(html, url="https://example.com/login")

        assert obs.page_state == PageState.LOGIN

    def test_login_warning(self) -> None:
        html = _read_fixture("login_page.html")
        obs = observe_html(html)

        assert any("login" in w for w in obs.warnings)


class TestReviewSubmitPage:
    def test_detects_review_state(self) -> None:
        html = _read_fixture("review_submit.html")
        obs = observe_html(html)

        assert obs.page_state == PageState.REVIEW

    def test_detects_dangerous_submit(self) -> None:
        html = _read_fixture("review_submit.html")
        obs = observe_html(html)

        submit_clickables = [
            c
            for c in obs.clickables
            if c.classification == ClickableClassification.DANGEROUS_SUBMIT
        ]
        assert len(submit_clickables) == 1


class TestCaptchaPage:
    def test_detects_captcha_state(self) -> None:
        html = _read_fixture("captcha_page.html")
        obs = observe_html(html)

        assert obs.page_state == PageState.CAPTCHA

    def test_captcha_warning(self) -> None:
        html = _read_fixture("captcha_page.html")
        obs = observe_html(html)

        assert any("captcha" in w for w in obs.warnings)


class TestUnknownPage:
    def test_unknown_page_state(self) -> None:
        html = "<html><body><h1>Hello World</h1><p>Nothing to see here.</p></body></html>"
        obs = observe_html(html)

        assert obs.page_state == PageState.UNKNOWN

    def test_no_clickables_on_plain_page(self) -> None:
        html = "<html><body><p>No buttons here.</p></body></html>"
        obs = observe_html(html)

        assert len(obs.clickables) == 0


class TestDisabledElements:
    def test_disabled_button_is_unknown(self) -> None:
        html = "<html><body><button disabled>Apply now</button></body></html>"
        obs = observe_html(html)

        # The disabled button should be classified as unknown.
        for c in obs.clickables:
            if "apply" in c.text.lower():
                assert c.classification == ClickableClassification.UNKNOWN
                assert c.enabled is False


class TestPlatformFixtures:
    """Tests using the platform fixture files from TESTING_STRATEGY."""

    def test_greenhouse_job_has_apply_link(self) -> None:
        html = _read_fixture_platform("greenhouse_job.html")
        obs = observe_html(html, url="https://boards.greenhouse.io/example/jobs/123")

        apply_clickables = [
            c for c in obs.clickables if c.classification == ClickableClassification.SAFE_APPLY
        ]
        assert len(apply_clickables) >= 1

    def test_lever_job_has_apply_button(self) -> None:
        html = _read_fixture_platform("lever_job.html")
        obs = observe_html(html, url="https://jobs.lever.co/example/123")

        apply_clickables = [
            c for c in obs.clickables if c.classification == ClickableClassification.SAFE_APPLY
        ]
        assert len(apply_clickables) >= 1

    def test_workday_login_detected(self) -> None:
        html = _read_fixture_platform("workday_login.html")
        obs = observe_html(html, url="https://example.myworkdayjobs.com/login")

        assert obs.page_state == PageState.LOGIN

    def test_unknown_custom_form_detected(self) -> None:
        html = _read_fixture_platform("unknown_custom_form.html")
        obs = observe_html(html, url="https://unknown-ats.com/apply/123")

        # Should detect either form state or at least inputs.
        assert obs.page_state in (PageState.FORM, PageState.UNKNOWN)
        assert len(obs.inputs) >= 3  # fullname, email, phone, linkedin

    def test_unknown_custom_form_has_dangerous_submit(self) -> None:
        html = _read_fixture_platform("unknown_custom_form.html")
        obs = observe_html(html, url="https://unknown-ats.com/apply/123")

        submit_clickables = [
            c
            for c in obs.clickables
            if c.classification == ClickableClassification.DANGEROUS_SUBMIT
        ]
        assert len(submit_clickables) >= 1


class TestAdditionalFixtures:
    """Tests using the additional fixture files from TESTING_STRATEGY."""

    def test_radio_checkbox_page_has_form(self) -> None:
        html = _read_fixture("radio_checkbox.html")
        obs = observe_html(html)

        assert obs.page_state == PageState.FORM
        assert len(obs.forms) == 1

    def test_select_dropdown_page_has_form(self) -> None:
        html = _read_fixture("select_dropdown.html")
        obs = observe_html(html)

        assert obs.page_state == PageState.FORM
        assert len(obs.forms) == 1


def _read_fixture_platform(name: str) -> str:
    """Read a fixture from tests/fixtures/platforms/."""
    return (Path(__file__).parent.parent / "fixtures" / "platforms" / name).read_text("utf-8")
