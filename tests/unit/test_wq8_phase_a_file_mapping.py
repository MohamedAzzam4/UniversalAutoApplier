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


def test_lebenslauf_maps_to_cv(candidate: CandidateProfile, job_with_cv: ApplicationJob) -> None:
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


def test_cv_label_maps_to_cv(candidate: CandidateProfile, job_with_cv: ApplicationJob) -> None:
    field = FormField(selector="lf-cv", name="cv", label="CV", type="file", required=True)
    mapping = map_field(field, candidate, job_with_cv)
    assert mapping is not None
    assert mapping.value == job_with_cv.cv_pdf


def test_anschreiben_maps_to_cover_letter(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-cover",
        name="coverLetter",
        label="Anschreiben",
        type="file",
        required=True,
    )
    mapping = map_field(field, candidate, job_with_cv)
    assert mapping is not None
    assert mapping.value == job_with_cv.cover_letter_pdf


def test_dokumente_not_automatically_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(selector="lf-doc", name="docs", label="Dokumente", type="file", required=True)
    assert map_field(field, candidate, job_with_cv) is None


def test_weitere_unterlagen_not_automatically_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-weitere",
        name="attachmentFile",
        label="Weitere Unterlagen",
        type="file",
        required=False,
    )
    assert map_field(field, candidate, job_with_cv) is None


def test_zeugnisse_unterlagen_not_automatically_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-zeug",
        name="docs",
        label="Zeugnisse / Unterlagen",
        type="file",
        required=False,
    )
    assert map_field(field, candidate, job_with_cv) is None


def test_vollstaendige_bewerbungsunterlagen_not_automatically_cv(
    candidate: CandidateProfile, job_with_cv: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-voll",
        name="attachmentFile",
        label="Vollständige Bewerbungsunterlagen:",
        type="file",
        required=True,
        nearby_text="Anschreiben, Lebenslauf, Zeugnisse",
    )
    assert map_field(field, candidate, job_with_cv) is None
