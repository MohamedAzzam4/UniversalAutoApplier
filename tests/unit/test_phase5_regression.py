"""Regression tests for Phase 3 and Phase 4 safety behavior.

These tests prove that Phase 5's additions did not break any safety
invariants from previous phases:
- Phase 3: dangerous submit never clicked, unknown not clicked, login/captcha/review stop.
- Phase 4: fill engine never submits, password fields blocked, unknown required not guessed.
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
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import safe_explore

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(first_name="John", last_name="Doe", email="john@example.com")


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


class TestPhase3SubmitSafety:
    def test_dangerous_submit_never_clicked(self) -> None:
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

    def test_login_stops_exploration(self) -> None:
        login_html = _read_fixture("login_page.html")

        def observe():
            return observe_html(login_html, url="https://example.com/login")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)
        assert result.stopped_reason == "login_required"

    def test_captcha_stops_exploration(self) -> None:
        captcha_html = _read_fixture("captcha_page.html")

        def observe():
            return observe_html(captcha_html, url="https://example.com/captcha")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)
        assert result.stopped_reason == "captcha_detected"

    def test_review_stops_exploration(self) -> None:
        review_html = _read_fixture("review_submit.html")

        def observe():
            return observe_html(review_html, url="https://example.com/review")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)
        assert result.stopped_reason == "review_page"


class TestPhase4FillSafety:
    def test_fill_engine_never_submits(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        for result in summary.results:
            assert "submit" not in result.status

    def test_password_field_blocked(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#pw", name="password", label="Password", type="unknown", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.blocked == 1
        assert summary.results[0].status == "blocked"
        assert summary.results[0].field_type == "password"

    def test_unknown_required_not_guessed(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=True),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.intervention_needed == 1
        assert summary.results[0].status == "intervention_needed"
        # The value should be None (not guessed).
        assert summary.results[0].value is None

    def test_unknown_optional_skipped(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#gpa", name="gpa", label="College GPA", type="text", required=False
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))

        assert summary.skipped == 1
        assert summary.results[0].status == "skipped"
