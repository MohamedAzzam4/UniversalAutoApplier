"""Tests for :mod:`universal_auto_applier.form_engine.field_mapper`.

Tests deterministic field mapping rules.
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
from universal_auto_applier.form_engine.field_mapper import (
    CONFIDENCE_THRESHOLD,
    map_field,
    map_fields,
)


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


def _make_job(
    tmp_path: Path, cv_pdf: str | None = None, cover_letter_pdf: str | None = None
) -> ApplicationJob:
    cv = cv_pdf or str(tmp_path / "cv.pdf")
    cover = cover_letter_pdf or str(tmp_path / "cover.pdf")
    # Create the files so they exist.
    Path(cv).write_bytes(b"fake")
    Path(cover).write_bytes(b"fake")
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
        cv_pdf=cv,
        cover_letter_pdf=cover,
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="job-123",
    )


class TestNameFields:
    def test_first_name(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#fn", name="first_name", label="First name", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "John"
        assert mapping.source == "candidate_profile"
        assert mapping.confidence >= CONFIDENCE_THRESHOLD

    def test_last_name(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#ln", name="last_name", label="Last name", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "Doe"

    def test_full_name(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#name", name="name", label="Full name", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "John Doe"


class TestContactFields:
    def test_email(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#email", name="email", label="Email address", type="email")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "john@example.com"

    def test_phone(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#phone", name="phone", label="Phone number", type="phone")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "+49 123 456789"

    def test_linkedin(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#linkedin", name="linkedin", label="LinkedIn URL", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "https://linkedin.com/in/johndoe"

    def test_github(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#github", name="github", label="GitHub profile", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert "github.com/johndoe" in mapping.value


class TestLocationFields:
    def test_city(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#city", name="city", label="City", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "Munich"

    def test_country(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#country", name="country", label="Country", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "Germany"


class TestSponsorshipField:
    def test_sponsorship_no(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(
            selector="#sponsorship",
            name="sponsorship",
            label="Do you require visa sponsorship?",
            type="radio",
        )
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.value == "No"
        assert mapping.source == "candidate_profile"


class TestFileFields:
    def test_resume_mapping(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#resume", name="resume", label="Upload resume", type="file")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.source == "document_path"
        assert mapping.value.endswith("cv.pdf")
        assert mapping.confidence == 0.99

    def test_cover_letter_mapping(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#cover", name="cover", label="Upload cover letter", type="file")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.source == "document_path"
        assert mapping.value.endswith("cover.pdf")

    def test_file_not_exist_low_confidence(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        # Create a job with a nonexistent CV path.
        job = _make_job(tmp_path)
        job.cv_pdf = "/nonexistent/cv.pdf"
        field = FormField(selector="#resume", name="resume", label="Upload resume", type="file")
        mapping = map_field(field, candidate, job)

        assert mapping is not None
        assert mapping.requires_user_confirmation is True
        assert "does not exist" in mapping.explanation


class TestNoMapping:
    def test_unknown_field_returns_none(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#gpa", name="gpa", label="College GPA", type="text")
        mapping = map_field(field, candidate, job)

        assert mapping is None

    def test_password_field_returns_none(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        field = FormField(selector="#pw", name="password", label="Password", type="unknown")
        mapping = map_field(field, candidate, job)

        assert mapping is None


class TestMapFields:
    def test_maps_multiple_fields(self, tmp_path: Path) -> None:
        candidate = _make_candidate()
        job = _make_job(tmp_path)
        fields = [
            FormField(selector="#fn", name="first_name", label="First name", type="text"),
            FormField(selector="#em", name="email", label="Email address", type="email"),
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text"),
        ]
        mappings = map_fields(fields, candidate, job)

        # Only 2 out of 3 should be mapped (GPA has no mapping).
        assert len(mappings) == 2
        assert mappings[0].value == "John"
        assert mappings[1].value == "john@example.com"
