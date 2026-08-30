"""Regression: intervention bridge + PLZ/Straße explicit-answer precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
from universal_auto_applier.form_engine.field_mapper import map_field


@pytest.fixture
def candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="Test", last_name="Candidate", email="a@b.c", city="Teststadt", country="Germany"
    )


@pytest.fixture
def job_base(tmp_path: Path) -> ApplicationJob:
    url = "https://example.com/jobs/bridge"
    return ApplicationJob(
        application_id=compute_application_id(platform="unknown", external_job_id=None, url=url),
        company="C",
        title="T",
        url=url,
        platform="unknown",
        source="test",
        verdict="apply",
        status="evaluated",
        metadata={},
    )


def test_plz_without_explicit_answer_no_mapping(
    candidate: CandidateProfile, job_base: ApplicationJob
) -> None:
    field = FormField(selector="lf-plz", name="plz", label="PLZ:*", type="text", required=True)
    assert map_field(field, candidate, job_base) is None


def test_plz_with_explicit_owner_answer_maps(
    candidate: CandidateProfile, job_base: ApplicationJob
) -> None:
    job_base.metadata["form_answers"] = {"PLZ:*": "91054"}
    field = FormField(selector="lf-plz", name="plz", label="PLZ:*", type="text", required=True)
    m = map_field(field, candidate, job_base)
    assert m is not None
    assert m.value == "91054"
    assert m.source == "application_job"


def test_strasse_without_explicit_answer_no_mapping(
    candidate: CandidateProfile, job_base: ApplicationJob
) -> None:
    field = FormField(
        selector="lf-str", name="strasse", label="Straße:*", type="text", required=True
    )
    assert map_field(field, candidate, job_base) is None


def test_strasse_with_explicit_owner_answer_maps(
    candidate: CandidateProfile, job_base: ApplicationJob
) -> None:
    job_base.metadata["form_answers"] = {"Straße:*": "Haagstr. 16"}
    field = FormField(
        selector="lf-str", name="strasse", label="Straße:*", type="text", required=True
    )
    m = map_field(field, candidate, job_base)
    assert m is not None
    assert m.value == "Haagstr. 16"
    assert m.source == "application_job"


def test_city_must_never_leak_into_plz_or_strasse(
    candidate: CandidateProfile, job_base: ApplicationJob
) -> None:
    # candidate has city Teststadt, but PLZ/Strasse must not be inferred from city
    field_plz = FormField(selector="lf-plz", name="plz", label="PLZ:*", type="text", required=True)
    field_str = FormField(
        selector="lf-str", name="strasse", label="Straße:*", type="text", required=True
    )
    assert map_field(field_plz, candidate, job_base) is None
    assert map_field(field_str, candidate, job_base) is None
    # Even with city present, PLZ/Strasse remain None without explicit answer
    assert candidate.city == "Teststadt"
    assert map_field(field_plz, candidate, job_base) is None
