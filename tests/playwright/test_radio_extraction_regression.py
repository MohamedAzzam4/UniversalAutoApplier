"""Playwright regression tests for radio-group extraction and typed-answer
validation against rendered fixture pages.

Coverage:

1. Fieldset + legend radio group → label IS the legend, not the option text.
2. ARIA-labelled radio group (aria-labelledby) → label IS the referenced text.
3. Radio group without fieldset → label falls back to container/name (not
   the option text "Yes"/"No").
4. Numeric field receiving invalid LLM text ("Yes") → status is
   ``intervention_needed`` (never ``failed``).
5. Select answer outside allowed options → status is ``intervention_needed``.
6. Valid radio answer matching one available option → status is ``filled``
   and ``filled_value`` is recorded separately.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext

from tests.playwright._fixture_server import serve_fixture_dir
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.llm.qa_service import MockQuestionAnsweringService

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


@pytest.fixture(scope="module")
def fixture_server() -> str:
    yield from serve_fixture_dir(FIXTURE_DIR)


def _make_job(
    tmp_path: Path,
    url: str,
    external_id: str,
    metadata: dict | None = None,
) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_md = tmp_path / f"{external_id}-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python automation, FastAPI, Docker, Kubernetes", encoding="utf-8")
    base_meta: dict = {
        "candidate_profile": {
            "first_name": "Mohamed",
            "last_name": "Azzam",
            "full_name": "Mohamed Azzam",
            "email": "mohamed@example.com",
            "phone": "+49 1234567",
            "requires_sponsorship": False,
        },
    }
    if metadata:
        base_meta.update(metadata)
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
        source="fixture",
        company="Fixture Company",
        title="Working Student",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id=external_id,
        documents=ApplicationJobDocuments(cv_md=str(cv_md)),
        metadata=base_meta,
    )


def _make_config(tmp_path: Path) -> LiveBrowserConfig:
    return LiveBrowserConfig(
        artifacts_root=tmp_path / "live-runs",
        profile_dir=None,
        headless=True,
        channel=None,
        timeout_ms=15_000,
        max_steps=5,
        capture_trace=False,
    )


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="Mohamed",
        last_name="Azzam",
        full_name="Mohamed Azzam",
        email="mohamed@example.com",
        phone="+49 1234567",
        requires_sponsorship=False,
    )


# ---------------------------------------------------------------------------
# 1. Fieldset + legend radio group
# ---------------------------------------------------------------------------


class TestFieldsetLegendRadioGroup:
    def test_label_is_legend_not_option_text(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Radio group inside <fieldset><legend>…</legend></fieldset> must
        produce one logical field whose label is the legend text — never
        the option text "Yes" or "No"."""
        url = f"{fixture_server}/radio_fieldset.html"
        job = _make_job(tmp_path, url, "radio-fs-1")
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-radio-fs",
        )

        # Find the radio group field (NOT the first_name text input).
        radios = [f for f in report.fields if f.field_type == "radio"]
        assert len(radios) == 1, f"Expected 1 radio group, got {len(radios)}"

        radio = radios[0]
        # Exact label is the legend text.
        assert radio.label == "Do you have experience with Kubernetes?", (
            f"Expected legend as label, got {radio.label!r}"
        )
        # NOT the option text.
        assert radio.label not in ("Yes", "No")
        # Options include Yes and No.
        assert "Yes" in radio.options
        assert "No" in radio.options
        # Stable group token exists.
        assert radio.field_token != ""
        # Final Submit not clicked.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 2. ARIA-labelled radio group (aria-labelledby)
# ---------------------------------------------------------------------------


class TestAriaLabelledByRadioGroup:
    def test_label_is_referenced_text(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Radio group inside <div role='group' aria-labelledby='…'> must
        use the referenced element's text as the label, not the option
        text."""
        url = f"{fixture_server}/radio_aria.html"
        job = _make_job(tmp_path, url, "radio-aria-1")
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-radio-aria",
        )

        radios = [f for f in report.fields if f.field_type == "radio"]
        assert len(radios) == 1, f"Expected 1 radio group, got {len(radios)}"

        radio = radios[0]
        # Exact label is the referenced element's text.
        assert radio.label == "Do you have experience with Kubernetes?", (
            f"Expected aria-labelledby target text as label, got {radio.label!r}"
        )
        # NOT the option text.
        assert radio.label not in ("Yes", "No")
        # Options include Yes and No.
        assert "Yes" in radio.options
        assert "No" in radio.options
        # Stable group token exists.
        assert radio.field_token != ""
        # Final Submit not clicked.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 3. Radio group without fieldset
# ---------------------------------------------------------------------------


class TestRadioGroupWithoutFieldset:
    def test_label_is_not_option_text(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """A radio group with no fieldset and no ARIA labelling must still
        NOT use the option text "Yes"/"No" as the label. The fallback is
        the group's name attribute or container text."""
        url = f"{fixture_server}/radio_no_fieldset.html"
        job = _make_job(tmp_path, url, "radio-bare-1")
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-radio-bare",
        )

        radios = [f for f in report.fields if f.field_type == "radio"]
        assert len(radios) == 1, f"Expected 1 radio group, got {len(radios)}"

        radio = radios[0]
        # The label must NOT be the option text.
        assert radio.label not in ("Yes", "No"), (
            f"Bare radio group label must not be option text, got {radio.label!r}"
        )
        # Options include Yes and No.
        assert "Yes" in radio.options
        assert "No" in radio.options
        # Stable group token exists.
        assert radio.field_token != ""
        # Final Submit not clicked.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 4. Numeric field receiving invalid LLM text
# ---------------------------------------------------------------------------


class TestNumericFieldRejectsInvalidLlmText:
    def test_number_field_getting_yes_yields_intervention_needed(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """An LLM answer of 'Yes' for a number field must be rejected
        BEFORE Playwright filling. Status is intervention_needed (never
        failed)."""
        url = f"{fixture_server}/invalid_typed_answers.html"
        job = _make_job(tmp_path, url, "invalid-num-1")
        config = _make_config(tmp_path)

        # Mock LLM that returns "Yes" for every question (wrong type for
        # the number field).
        qa_service = MockQuestionAnsweringService(
            answer="Yes",
            confidence=0.9,
            evidence_facts=["CV mentions Docker"],
            explanation="CV states Docker experience",
        )

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-invalid-num",
            qa_service=qa_service,
        )

        # Find the years-of-Docker number field.
        numbers = [f for f in report.fields if f.field_type == "number"]
        assert len(numbers) >= 1, f"Expected a number field, got {numbers}"
        years = next(
            (f for f in numbers if "years" in f.label.lower() or "docker" in f.label.lower()),
            None,
        )
        assert years is not None, f"Years-of-Docker field not found: {numbers}"

        # Exact status: intervention_needed (NOT failed). The LLM proposed
        # "Yes" which fails number validation, so the executor rejects it
        # without calling Playwright.
        assert years.status == "intervention_needed", (
            f"Expected intervention_needed for invalid number answer, got "
            f"{years.status!r}. Explanation: {years.explanation!r}"
        )

        # Status failed is prohibited anywhere in the report.
        failed = [f for f in report.fields if f.status == "failed"]
        assert failed == [], f"No field should be 'failed', got: {failed}"

        # Final Submit not clicked.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 5. Select answer outside allowed options
# ---------------------------------------------------------------------------


class TestSelectAnswerOutsideOptions:
    def test_select_outside_options_yields_intervention_needed(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """An LLM answer of 'France' for a country select that only has
        Germany/United States as options must be rejected. Status is
        intervention_needed (never failed)."""
        url = f"{fixture_server}/invalid_typed_answers.html"
        job = _make_job(tmp_path, url, "invalid-sel-1")
        config = _make_config(tmp_path)

        qa_service = MockQuestionAnsweringService(
            answer="France",
            confidence=0.9,
            evidence_facts=["Candidate profile says France"],
            explanation="Profile indicates France",
        )

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-invalid-sel",
            qa_service=qa_service,
        )

        # Find the country select.
        selects = [f for f in report.fields if f.field_type == "select"]
        assert len(selects) >= 1, f"Expected a select field, got {selects}"
        country = next(
            (f for f in selects if "country" in f.label.lower()),
            None,
        )
        assert country is not None, f"Country select not found: {selects}"

        # The select must list Germany and United States as options.
        option_labels_lower = [o.lower() for o in country.options]
        assert any("germany" in o for o in option_labels_lower), (
            f"Germany option missing: {country.options}"
        )
        assert any("united states" in o for o in option_labels_lower), (
            f"United States option missing: {country.options}"
        )

        # Exact status: intervention_needed (NOT failed).
        assert country.status == "intervention_needed", (
            f"Expected intervention_needed for out-of-options answer, got "
            f"{country.status!r}. Explanation: {country.explanation!r}"
        )

        # Status failed is prohibited.
        failed = [f for f in report.fields if f.status == "failed"]
        assert failed == [], f"No field should be 'failed', got: {failed}"

        # Final Submit not clicked.
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 6. Valid radio answer matching one available option
# ---------------------------------------------------------------------------


class TestValidRadioAnswerMatchesOption:
    def test_valid_radio_answer_fills_and_records_value(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """A valid radio answer (e.g. 'Yes') that matches one available
        option must be filled successfully. The filled value is recorded
        separately in ``filled_value``."""
        url = f"{fixture_server}/radio_fieldset.html"
        job = _make_job(tmp_path, url, "valid-radio-1")
        config = _make_config(tmp_path)

        qa_service = MockQuestionAnsweringService(
            answer="Yes",
            confidence=0.9,
            evidence_facts=["CV mentions Kubernetes"],
            explanation="CV states Kubernetes experience",
        )

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-valid-radio",
            qa_service=qa_service,
        )

        radios = [f for f in report.fields if f.field_type == "radio"]
        assert len(radios) == 1, f"Expected 1 radio group, got {len(radios)}"

        radio = radios[0]
        # The radio group should be filled (deterministic positive-evidence
        # mapping handles it because the CV mentions Kubernetes).
        assert radio.status == "filled", (
            f"Expected filled, got {radio.status!r}. Explanation: {radio.explanation!r}"
        )
        # Filled value recorded separately, distinct from option labels.
        assert radio.filled_value in ("Yes", "No"), (
            f"Filled value must be one of the available options, got {radio.filled_value!r}"
        )
        assert radio.filled_value != "", "Filled value should not be empty"
        # Options are still listed.
        assert "Yes" in radio.options
        assert "No" in radio.options
        # Final Submit not clicked.
        assert report.submitted is False
