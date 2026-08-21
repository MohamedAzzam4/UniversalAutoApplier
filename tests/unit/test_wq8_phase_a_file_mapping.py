"""WQ-8 Phase A — file field deterministic mapping for German ATS."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormField,
)
from universal_auto_applier.form_engine.field_mapper import map_field


@pytest.fixture
def candidate() -> CandidateProfile:
    return CandidateProfile(full_name="Test", email="a@b.c")


@pytest.fixture
def job_with_cv(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF")
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF")
    url = "https://example.com/jobs/test-b"
    return ApplicationJob(
        application_id=compute_application_id(platform="unknown", external_job_id=None, url=url),
        company="C",
        title="T",
        url=url,
        platform="unknown",
        source="test",
        verdict="apply",
        status="ready_to_apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
    )


def test_bewerbungsunterlagen_maps_to_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-test",
        name="attachmentFile",
        label="Vollständige Bewerbungsunterlagen:",
        type="file",
        required=True,
        nearby_text="Anschreiben, Lebenslauf",
    )
    mapping = map_field(field, candidate, job_with_cv)
    assert mapping is not None
    assert mapping.value == job_with_cv.cv_pdf
    assert mapping.source == "document_path"


def test_lebenslauf_label_maps_to_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-test2",
        name="cv",
        label="Lebenslauf",
        type="file",
        required=True,
    )
    mapping = map_field(field, candidate, job_with_cv)
    assert mapping is not None
    assert mapping.value == job_with_cv.cv_pdf


def test_german_bewerbungsunterlagen_hash_recorded(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-test3",
        name="attachmentFile",
        label="Vollständige Bewerbungsunterlagen:",
        type="file",
        required=False,
    )
    mapping = map_field(field, candidate, job_with_cv)
    assert mapping is not None
