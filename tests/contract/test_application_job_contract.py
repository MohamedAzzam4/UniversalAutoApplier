"""Contract tests for the :class:`ApplicationJob` Pydantic v2 model.

These protect the boundary between JobHunter and UniversalAutoApplier. Per
``DATA_CONTRACTS.md``, the schema is the contract; any change here is a
contract change and must be reviewed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform


def _valid_job_data(**overrides: object) -> dict[str, object]:
    """Return minimal valid ApplicationJob data with optional overrides."""
    url = "https://example.com/jobs/123"
    platform = "greenhouse"
    external_job_id = "job-123"
    application_id = compute_application_id(
        platform=platform, external_job_id=external_job_id, url=url
    )
    base: dict[str, object] = {
        "application_id": application_id,
        "platform": platform,
        "source": "linkedin",
        "company": "Example GmbH",
        "title": "Working Student AI",
        "url": url,
        "location": "Munich, Germany",
        "job_description": "Full job description text",
        "score": 4.1,
        "verdict": "apply",
        "cv_pdf": str(Path("/tmp/example-cv.pdf")),
        "cover_letter_pdf": str(Path("/tmp/example-cover.pdf")),
        "status": "ready_to_apply",
        "external_job_id": external_job_id,
    }
    base.update(overrides)
    return base


class TestApplicationJobValid:
    def test_minimal_valid_job(self) -> None:
        data = _valid_job_data()
        job = ApplicationJob(**data)
        assert job.application_id == data["application_id"]
        assert job.platform == Platform.GREENHOUSE
        assert job.status == ApplicationStatus.READY_TO_APPLY
        assert job.score == 4.1

    def test_with_optional_fields(self) -> None:
        data = _valid_job_data(
            job_id="platform-id-42",
            date_posted="2026-07-10",
            evaluation_reason="Score 4.1 >= threshold",
            german_filter_result="passed",
            metadata={"original_source_row": 17},
        )
        job = ApplicationJob(**data)
        assert job.job_id == "platform-id-42"
        assert job.date_posted == "2026-07-10"
        assert job.metadata == {"original_source_row": 17}

    def test_documents_optional(self) -> None:
        data = _valid_job_data(
            documents={
                "cv_md": str(Path("/tmp/cv.md")),
                "cover_letter_md": str(Path("/tmp/cover.md")),
            }
        )
        job = ApplicationJob(**data)
        assert job.documents is not None
        assert job.documents.cv_md is not None

    def test_score_zero_is_valid(self) -> None:
        data = _valid_job_data(score=0.0, status="evaluated", cv_pdf=None, cover_letter_pdf=None)
        job = ApplicationJob(**data)
        assert job.score == 0.0

    def test_unknown_metadata_preserved(self) -> None:
        data = _valid_job_data(metadata={"custom_field": "value", "number": 42})
        job = ApplicationJob(**data)
        assert job.metadata["custom_field"] == "value"
        assert job.metadata["number"] == 42


class TestApplicationJobInvalidUrl:
    def test_rejects_ftp_url(self) -> None:
        data = _valid_job_data(url="ftp://example.com/jobs/123")
        with pytest.raises(ValidationError, match="HTTP or HTTPS"):
            ApplicationJob(**data)

    def test_rejects_javascript_url(self) -> None:
        data = _valid_job_data(url="javascript:alert(1)")
        with pytest.raises(ValidationError):
            ApplicationJob(**data)

    def test_rejects_missing_hostname(self) -> None:
        data = _valid_job_data(url="https://")
        with pytest.raises(ValidationError, match="hostname"):
            ApplicationJob(**data)


class TestApplicationJobInvalidVerdict:
    @pytest.mark.parametrize("bad_verdict", ["", "yes", "maybe", "APPLY", "apply "])
    def test_rejects_invalid_verdict(self, bad_verdict: str) -> None:
        data = _valid_job_data(verdict=bad_verdict)
        with pytest.raises(ValidationError, match="verdict"):
            ApplicationJob(**data)

    @pytest.mark.parametrize("good_verdict", ["apply", "consider", "skip"])
    def test_accepts_valid_verdicts(self, good_verdict: str) -> None:
        data = _valid_job_data(
            verdict=good_verdict, status="evaluated", cv_pdf=None, cover_letter_pdf=None
        )
        job = ApplicationJob(**data)
        assert job.verdict == good_verdict


class TestApplicationJobDocumentsRequired:
    def test_ready_to_apply_requires_cv_pdf(self) -> None:
        data = _valid_job_data(cv_pdf=None, status="ready_to_apply")
        with pytest.raises(ValidationError, match="cv_pdf is required"):
            ApplicationJob(**data)

    def test_ready_to_apply_requires_cover_letter(self) -> None:
        data = _valid_job_data(cover_letter_pdf=None, status="ready_to_apply")
        with pytest.raises(ValidationError, match="cover_letter_pdf is required"):
            ApplicationJob(**data)

    def test_non_ready_status_allows_missing_documents(self) -> None:
        data = _valid_job_data(cv_pdf=None, cover_letter_pdf=None, status="evaluated")
        job = ApplicationJob(**data)
        assert job.cv_pdf is None
        assert job.cover_letter_pdf is None


class TestApplicationJobIdDeterminism:
    def test_id_matches_deterministic_computation(self) -> None:
        data = _valid_job_data()
        job = ApplicationJob(**data)
        expected = compute_application_id(
            platform=str(job.platform),
            external_job_id=job.external_job_id,
            url=job.url,
        )
        assert job.application_id == expected

    def test_rejects_wrong_application_id(self) -> None:
        data = _valid_job_data(application_id="0" * 64)
        with pytest.raises(ValidationError, match="does not match"):
            ApplicationJob(**data)

    def test_id_uses_canonical_url_when_no_external_job_id(self) -> None:
        url = "https://Example.com/jobs/123/"
        application_id = compute_application_id(platform=None, external_job_id=None, url=url)
        data = _valid_job_data(
            url=url,
            external_job_id=None,
            application_id=application_id,
            status="evaluated",
            cv_pdf=None,
            cover_letter_pdf=None,
        )
        job = ApplicationJob(**data)
        assert job.application_id == application_id


class TestApplicationJobDatePosted:
    def test_valid_date(self) -> None:
        data = _valid_job_data(
            date_posted="2026-07-10", status="evaluated", cv_pdf=None, cover_letter_pdf=None
        )
        job = ApplicationJob(**data)
        assert job.date_posted == "2026-07-10"

    def test_invalid_date_format(self) -> None:
        data = _valid_job_data(
            date_posted="10-07-2026", status="evaluated", cv_pdf=None, cover_letter_pdf=None
        )
        with pytest.raises(ValidationError):
            ApplicationJob(**data)


class TestApplicationJobScore:
    def test_negative_score_rejected(self) -> None:
        data = _valid_job_data(score=-1.0, status="evaluated", cv_pdf=None, cover_letter_pdf=None)
        with pytest.raises(ValidationError):
            ApplicationJob(**data)

    def test_score_can_be_none(self) -> None:
        data = _valid_job_data(score=None, status="evaluated", cv_pdf=None, cover_letter_pdf=None)
        job = ApplicationJob(**data)
        assert job.score is None
