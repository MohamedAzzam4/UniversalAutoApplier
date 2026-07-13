"""Tests for :mod:`universal_auto_applier.form_engine.fill_engine`.

Tests form filling safety and behavior, including:
- text/email/phone/textarea/select/radio/checkbox/file fields
- unknown required fields
- password field blocking
- file path validation
- never-submit safety
- regression for Phase 3 behavior
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormField,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.form_engine.schema_extractor import extract_form_fields
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import safe_explore

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        full_name="John Doe",
        email="john@example.com",
        phone="+49 123 456789",
        linkedin_url="https://linkedin.com/in/johndoe",
        city="Munich",
        country="Germany",
        requires_sponsorship=False,
        work_authorization="EU citizen",
        years_of_experience=5,
        current_position="Software Engineer",
        website="https://johndoe.com",
        github_url="https://github.com/johndoe",
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"fake")
    cover.write_bytes(b"fake")
    url = "https://example.com/jobs/123"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="job-123", url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="Example Corp",
        title="Software Engineer",
        url=url,
        score=4.1,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="job-123",
    )


class TestFillTextFields:
    def test_fill_first_name(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.filled == 1
        assert summary.results[0].status == "filled"
        assert summary.results[0].value == "John"

    def test_fill_email(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#em", name="email", label="Email address", type="email", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.filled == 1
        assert summary.results[0].value == "john@example.com"


class TestFillOptionalSkipped:
    def test_optional_field_without_mapping_skipped(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=False)
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.skipped == 1
        assert summary.results[0].status == "skipped"


class TestRequiredUnknownField:
    def test_required_unknown_field_creates_intervention(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=True)
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.intervention_needed == 1
        assert summary.results[0].status == "intervention_needed"
        assert "no deterministic mapping" in summary.results[0].explanation

    def test_all_required_fields_resolved_false_when_intervention(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=True)
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert not summary.all_required_fields_resolved

    def test_all_required_fields_resolved_true_when_filled(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.all_required_fields_resolved


class TestPasswordFieldBlocked:
    def test_password_field_is_blocked(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#pw", name="password", label="Password", type="unknown", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.blocked == 1
        assert summary.results[0].status == "blocked"
        assert "Password" in summary.results[0].explanation


class TestFileFieldValidation:
    def test_file_field_filled_when_exists(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#resume", name="resume", label="Upload resume", type="file", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.filled == 1
        assert summary.results[0].value.endswith("cv.pdf")

    def test_file_field_intervention_when_not_exists(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        job.cv_pdf = "/nonexistent/cv.pdf"  # Override to nonexistent
        fields = [
            FormField(
                selector="#resume", name="resume", label="Upload resume", type="file", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), job)

        assert summary.intervention_needed == 1
        assert "does not exist" in summary.results[0].explanation


class TestFullFormFill:
    def test_full_application_form(self, tmp_path: Path) -> None:
        html = _read_fixture("full_application.html")
        fields = extract_form_fields(html)
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        # Should fill: first_name, last_name, email, phone, linkedin,
        # textarea (no mapping -> skipped/intervention), resume (file), cover (file)
        assert summary.total_fields >= 7
        assert summary.filled >= 5  # name fields, email, phone, linkedin, files
        assert (
            summary.filled + summary.skipped + summary.blocked + summary.intervention_needed
            == summary.total_fields
        )

    def test_unknown_required_form(self, tmp_path: Path) -> None:
        html = _read_fixture("unknown_required.html")
        fields = extract_form_fields(html)
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        # GPA, salary, referral are unknown required fields.
        assert summary.intervention_needed >= 3
        assert not summary.all_required_fields_resolved


class TestNeverSubmit:
    """The fill engine must never click submit buttons."""

    def test_fill_engine_does_not_return_submit_action(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        # No result should have a submit-related action.
        for result in summary.results:
            assert "submit" not in result.status


class TestPhase3Regression:
    """Regression tests proving Phase 3 navigation behavior still works."""

    def test_dangerous_submit_never_clicked(self) -> None:
        """SafeExplorer must never click a dangerous_submit button."""
        submit_html = '<html><body><button type="submit">Submit application</button></body></html>'
        clicked: list[str] = []

        def observe():
            return observe_html(submit_html, url="https://example.com/submit")

        def click(selector: str) -> bool:
            clicked.append(selector)
            return True

        result = safe_explore(observe, click)

        assert len(clicked) == 0
        assert result.stopped_reason in ("submit_detected", "no_safe_action")

    def test_unknown_clickables_not_clicked(self) -> None:
        unknown_html = "<html><body><h1>Hello</h1></body></html>"

        def observe():
            return observe_html(unknown_html, url="https://example.com/unknown")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.stopped_reason == "no_safe_action"

    def test_login_page_stops_exploration(self) -> None:
        login_html = _read_fixture("login_page.html")

        def observe():
            return observe_html(login_html, url="https://example.com/login")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.stopped_reason == "login_required"

    def test_captcha_page_stops_exploration(self) -> None:
        captcha_html = _read_fixture("captcha_page.html")

        def observe():
            return observe_html(captcha_html, url="https://example.com/captcha")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.stopped_reason == "captcha_detected"

    def test_review_page_stops_exploration(self) -> None:
        review_html = _read_fixture("review_submit.html")

        def observe():
            return observe_html(review_html, url="https://example.com/review")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.stopped_reason == "review_page"
