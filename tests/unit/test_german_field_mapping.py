"""Bilingual deterministic mapping — German ATS labels.

Proves that high-confidence German aliases map to the same canonical
CandidateProfile fields as English, without embeddings or LLM, and that
ambiguous/sensitive German labels are NOT guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
from universal_auto_applier.form_engine.field_mapper import map_field


@pytest.fixture
def candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="Test",
        last_name="Candidate",
        full_name="Test Candidate",
        email="test.candidate@example.com",
        phone="+1 555 0100",
        city="Teststadt",
        country="Germany",
    )


@pytest.fixture
def job(tmp_path: Path) -> ApplicationJob:
    url = "https://example.com/jobs/de-test"
    return ApplicationJob(
        application_id=compute_application_id(platform="unknown", external_job_id=None, url=url),
        company="TestCo",
        title="Test",
        url=url,
        platform="unknown",
        source="test",
        verdict="apply",
        status="evaluated",
        metadata={},
    )


def _field(label: str, type_: str = "text") -> FormField:
    return FormField(selector="lf-test", name="", label=label, type=type_, required=True)


def test_vorname_maps_to_first_name(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Vorname:*"), candidate, job).value == "Test"


def test_nachname_maps_to_last_name(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Nachname:*"), candidate, job).value == "Candidate"


def test_familienname_maps_to_last_name(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Familienname"), candidate, job).value == "Candidate"


def test_first_name_english_still_maps(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("First Name"), candidate, job).value == "Test"
    assert (
        map_field(_field("Vorname:*"), candidate, job).value
        == map_field(_field("First Name"), candidate, job).value
    )


def test_last_name_bilingual_equivalence(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert (
        map_field(_field("Last Name"), candidate, job).value
        == map_field(_field("Nachname:*"), candidate, job).value
    )


def test_email_german_maps(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("E-Mail-Adresse:*", "email"), candidate, job).value == candidate.email
    assert map_field(_field("E-Mail"), candidate, job).value == candidate.email


def test_telefon_maps_to_phone(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Telefon:*"), candidate, job).value == candidate.phone


def test_telefonnummer_maps_to_phone(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Telefonnummer"), candidate, job).value == candidate.phone


def test_mobiltelefon_maps_to_phone(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Mobiltelefon"), candidate, job).value == candidate.phone


def test_phone_english_still_maps(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Phone"), candidate, job).value == candidate.phone


def test_land_maps_to_country(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Land:*"), candidate, job).value == "Germany"


def test_ort_maps_to_city(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Ort:*"), candidate, job).value == "Teststadt"


def test_wohnort_maps_to_city(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Wohnort"), candidate, job).value == "Teststadt"


def test_stadt_maps_to_city(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Stadt"), candidate, job).value == "Teststadt"


def test_city_english_still_maps(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("City"), candidate, job).value == "Teststadt"


def test_country_english_still_maps(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Country"), candidate, job).value == "Germany"


def test_gehaltsvorstellung_not_mapped(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Gehaltsvorstellung:*"), candidate, job) is None


def test_reisebereitschaft_not_mapped(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Reisebereitschaft:*"), candidate, job) is None


def test_kuendigungsfrist_not_mapped(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Kündigungsfrist:*"), candidate, job) is None


def test_schwerbehinderung_not_mapped(candidate: CandidateProfile, job: ApplicationJob) -> None:
    assert map_field(_field("Schwerbehinderung / Gleichstellung:*"), candidate, job) is None


def test_strasse_not_mapped_without_candidate_field(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    assert map_field(_field("Straße:*"), candidate, job) is None


def test_plz_not_mapped_without_candidate_field(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    assert map_field(_field("PLZ:*"), candidate, job) is None
    assert map_field(_field("Postleitzahl:*"), candidate, job) is None


def test_country_select_germany_option_selects_germany(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    from universal_auto_applier.core.models import FieldOption

    field = FormField(
        selector="lf-country",
        name="country",
        label="Country",
        type="select",
        required=True,
        options=[
            FieldOption(value="Germany", label="Germany"),
            FieldOption(value="France", label="France"),
        ],
    )
    assert map_field(field, candidate, job).value == "Germany"


def test_land_select_deutschland_alias_selects_deutschland(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    from universal_auto_applier.core.models import FieldOption

    field = FormField(
        selector="lf-land",
        name="country",
        label="Land:*",
        type="select",
        required=True,
        options=[
            FieldOption(value="Deutschland", label="Deutschland"),
            FieldOption(value="Österreich", label="Österreich"),
            FieldOption(value="Schweiz", label="Schweiz"),
        ],
    )
    assert map_field(field, candidate, job).value == "Deutschland"


def test_unrelated_select_with_deutschland_not_treated_as_country(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    from universal_auto_applier.core.models import FieldOption

    field = FormField(
        selector="lf-doc",
        name="docs",
        label="Dokumente",
        type="select",
        required=True,
        options=[
            FieldOption(value="Deutschland", label="Deutschland"),
            FieldOption(value="Österreich", label="Österreich"),
        ],
    )
    assert map_field(field, candidate, job) is None


def test_country_select_ambiguous_returns_intervention(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    from universal_auto_applier.core.models import FieldOption

    field = FormField(
        selector="lf-amb",
        name="country",
        label="Land:*",
        type="select",
        required=True,
        options=[
            FieldOption(value="Deutschland", label="Deutschland"),
            FieldOption(value="Germany", label="Germany"),
        ],
    )
    assert map_field(field, candidate, job) is None


def test_country_select_no_matching_option_returns_intervention(
    candidate: CandidateProfile, job: ApplicationJob
) -> None:
    from universal_auto_applier.core.models import FieldOption

    field = FormField(
        selector="lf-nomatch",
        name="country",
        label="Land:*",
        type="select",
        required=True,
        options=[
            FieldOption(value="Frankreich", label="Frankreich"),
            FieldOption(value="Italien", label="Italien"),
        ],
    )
    assert map_field(field, candidate, job) is None
