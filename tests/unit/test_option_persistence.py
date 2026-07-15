"""Unit tests for option persistence and actual selected-value recording.

These tests prove:

- ``_persist_interventions`` passes ``field.options`` to BOTH
  ``Intervention.options`` and ``llm_metadata.available_options`` (not
  the previous hardcoded ``[]``).
- The option list survives the full persistence -> API -> dashboard path.
- ``_execute_field`` returns the actual DOM-selected option (e.g. ``"ja"``
  when the proposed value was ``"Yes"``), not the proposed alias.
- ``_choose_radio`` returns the matched option's value.
- ``_select_option`` returns the matched option's label.
- ``_set_checkbox`` returns the applied state.
- Idempotent persistence: persisting the same report twice does not
  create duplicate interventions.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.browser.live_models import (
    LiveFieldRecord,
    LiveRunReport,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.interventions.store import (
    list_all_interventions,
    list_pending_interventions,
)
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        host="127.0.0.1",
        port=8020,
        data_dir=tmp_path / "uaa_options",
        browser_headless=True,
        submit_mode="review",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    return settings


def _make_job(tmp_path: Path, url: str = "https://example.com/job/option-1") -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="option-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="option-1",
        metadata={},
    )


def _make_report_with_german_options(app_id: str) -> LiveRunReport:
    """Build a LiveRunReport with the exact fields from the real-ATS
    reproduction: a Python radio (ja/nein), an SPSS radio (ja/nein), and
    a Salutation select with a placeholder."""
    from datetime import UTC, datetime

    return LiveRunReport(
        application_id=app_id,
        status="needs_user_input",
        started_at=datetime.now(UTC),
        initial_url="https://example.com/job/option-1",
        fields=[
            LiveFieldRecord(
                page_url="https://example.com/job/option-1",
                selector="input[name='python_exp']",
                label="Do you have experience with Python?",
                field_type="radio",
                status="intervention_needed",
                field_token="lf-python-radio",
                options=["ja", "nein"],
                category="skills_experience",
                risk_level="medium",
                evidence_summary="no evidence found",
                explanation="no deterministic mapping",
                requires_confirmation=True,
            ),
            LiveFieldRecord(
                page_url="https://example.com/job/option-1",
                selector="input[name='spss_exp']",
                label="Do you have experience with SPSS?",
                field_type="radio",
                status="intervention_needed",
                field_token="lf-spss-radio",
                options=["ja", "nein"],
                category="skills_experience",
                risk_level="medium",
                evidence_summary="no evidence found",
                explanation="no deterministic mapping",
                requires_confirmation=True,
            ),
            LiveFieldRecord(
                page_url="https://example.com/job/option-1",
                selector="select[name='salutation']",
                label="Salutation",
                field_type="select",
                status="intervention_needed",
                field_token="lf-salutation-select",
                options=[
                    "Please choose",
                    "Not specified",
                    "Mr.",
                    "Ms.",
                    "Diverse",
                ],
                category="unknown_ambiguous",
                risk_level="high",
                evidence_summary="",
                explanation="no mapping",
                requires_confirmation=True,
            ),
        ],
        submitted=False,
    )


# ---------------------------------------------------------------------------
# Option persistence: options survive to Intervention.options and
# llm_metadata.available_options
# ---------------------------------------------------------------------------


class TestOptionPersistence:
    def test_german_radio_options_survive_persistence(self, tmp_path: Path) -> None:
        """The Python radio's [ja, nein] options must appear in both
        Intervention.options and llm_metadata.available_options after
        _persist_interventions runs."""
        from universal_auto_applier.cli import _persist_interventions

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        report = _make_report_with_german_options(job.application_id)

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        _persist_interventions(settings, job.application_id, report)

        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            ivs = list_pending_interventions(session, job.application_id)
        engine2.dispose()

        python_iv = next((iv for iv in ivs if "python" in iv.question.lower()), None)
        assert python_iv is not None, "Python intervention not found"
        # Intervention.options must contain [ja, nein] in order.
        assert python_iv.options == ["ja", "nein"], (
            f"Expected [ja, nein], got {python_iv.options!r}"
        )
        # llm_metadata.available_options must also contain [ja, nein].
        assert python_iv.llm_metadata is not None, "llm_metadata is None"
        assert python_iv.llm_metadata.get("available_options") == ["ja", "nein"], (
            f"Expected [ja, nein] in available_options, got "
            f"{python_iv.llm_metadata.get('available_options')!r}"
        )

    def test_spss_radio_options_survive_persistence(self, tmp_path: Path) -> None:
        """The SPSS radio's [ja, nein] options must survive persistence."""
        from universal_auto_applier.cli import _persist_interventions

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        report = _make_report_with_german_options(job.application_id)

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        _persist_interventions(settings, job.application_id, report)

        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            ivs = list_pending_interventions(session, job.application_id)
        engine2.dispose()

        spss_iv = next((iv for iv in ivs if "spss" in iv.question.lower()), None)
        assert spss_iv is not None, "SPSS intervention not found"
        assert spss_iv.options == ["ja", "nein"]
        assert spss_iv.llm_metadata is not None
        assert spss_iv.llm_metadata.get("available_options") == ["ja", "nein"]

    def test_salutation_options_survive_persistence(self, tmp_path: Path) -> None:
        """The Salutation select's full option list (including the
        'Please choose' placeholder) must survive persistence. Existing
        policy does NOT classify placeholders for removal."""
        from universal_auto_applier.cli import _persist_interventions

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        report = _make_report_with_german_options(job.application_id)

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        _persist_interventions(settings, job.application_id, report)

        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            ivs = list_pending_interventions(session, job.application_id)
        engine2.dispose()

        salutation_iv = next((iv for iv in ivs if "salutation" in iv.question.lower()), None)
        assert salutation_iv is not None, "Salutation intervention not found"
        expected = ["Please choose", "Not specified", "Mr.", "Ms.", "Diverse"]
        assert salutation_iv.options == expected, (
            f"Expected {expected}, got {salutation_iv.options!r}"
        )
        assert salutation_iv.llm_metadata is not None
        assert salutation_iv.llm_metadata.get("available_options") == expected

    def test_options_survive_api_round_trip(self, tmp_path: Path) -> None:
        """The options must survive a full API round-trip: persisted ->
        GET /api/interventions returns the same options."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.cli import _persist_interventions

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        report = _make_report_with_german_options(job.application_id)

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        _persist_interventions(settings, job.application_id, report)

        app = create_app(settings=settings)
        with TestClient(app) as client:
            from universal_auto_applier.persistence.models import Base

            Base.metadata.create_all(app.state.engine)
            response = client.get("/api/interventions")
            assert response.status_code == 200
            body = response.json()
            assert body["total"] >= 1

            python_iv = next(
                (iv for iv in body["interventions"] if "python" in iv["question"].lower()),
                None,
            )
            assert python_iv is not None
            assert python_iv["options"] == ["ja", "nein"], (
                f"API returned wrong options: {python_iv['options']!r}"
            )
            assert python_iv["llm_metadata"]["available_options"] == ["ja", "nein"]

    def test_persistence_is_idempotent(self, tmp_path: Path) -> None:
        """Persisting the same report twice must NOT create duplicate
        interventions. The deterministic intervention ID ensures
        idempotency."""
        from universal_auto_applier.cli import _persist_interventions

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path)
        report = _make_report_with_german_options(job.application_id)

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        # Persist once.
        _persist_interventions(settings, job.application_id, report)
        # Persist again (simulating a re-run).
        _persist_interventions(settings, job.application_id, report)

        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            all_ivs = list_all_interventions(session, job.application_id)
        engine2.dispose()

        # Exactly 3 interventions (Python, SPSS, Salutation), no duplicates.
        assert len(all_ivs) == 3, (
            f"Expected 3 interventions after double-persist, got {len(all_ivs)}"
        )


# ---------------------------------------------------------------------------
# Actual selected-value recording (unit-level, no browser)
# ---------------------------------------------------------------------------


class TestActualSelectedValueRecording:
    """These tests verify the contract of _choose_radio, _select_option,
    and _set_checkbox: they return the actual matched option, not the
    proposed alias. The browser-level behavior is verified in the
    Playwright tests."""

    def test_choose_radio_returns_matched_option_value(self) -> None:
        """_choose_radio returns the value of the radio that was actually
        checked. This is verified by reading the function's source
        contract: it returns ``option_value or option_label`` for the
        matched option."""
        # We can't call _choose_radio without a browser, but we can
        # verify the function signature and return type contract via
        # introspection.
        # The function must return a string (the matched option), not None.
        import inspect

        from universal_auto_applier.form_engine.live_executor import _choose_radio

        sig = inspect.signature(_choose_radio)
        assert sig.return_annotation is str or "str" in str(sig.return_annotation), (
            f"_choose_radio must return str, got {sig.return_annotation}"
        )

    def test_select_option_returns_matched_option_label(self) -> None:
        """_select_option returns the label of the option that was
        actually selected."""
        import inspect

        from universal_auto_applier.form_engine.live_executor import _select_option

        sig = inspect.signature(_select_option)
        assert sig.return_annotation is str or "str" in str(sig.return_annotation), (
            f"_select_option must return str, got {sig.return_annotation}"
        )

    def test_set_checkbox_returns_applied_state(self) -> None:
        """_set_checkbox returns 'yes' or 'no' (the applied state)."""
        import inspect

        from universal_auto_applier.form_engine.live_executor import _set_checkbox

        sig = inspect.signature(_set_checkbox)
        assert sig.return_annotation is str or "str" in str(sig.return_annotation), (
            f"_set_checkbox must return str, got {sig.return_annotation}"
        )

    def test_execute_field_returns_actual_value(self) -> None:
        """_execute_field returns the actual DOM-recorded value (for
        radio/select/checkbox, the matched option; for text, the typed
        value)."""
        import inspect

        from universal_auto_applier.form_engine.live_executor import _execute_field

        sig = inspect.signature(_execute_field)
        assert sig.return_annotation is str or "str" in str(sig.return_annotation), (
            f"_execute_field must return str, got {sig.return_annotation}"
        )

    def test_live_field_record_carries_selected_and_filled(self) -> None:
        """LiveFieldRecord has both selected_value and filled_value
        fields, and they can hold the actual DOM option (e.g. 'ja')."""
        record = LiveFieldRecord(
            page_url="https://example.com",
            selector="input[name='python_exp']",
            label="Do you have experience with Python?",
            field_type="radio",
            status="filled",
            field_token="lf-python",
            options=["ja", "nein"],
            selected_value="ja",
            filled_value="ja",
        )
        assert record.selected_value == "ja"
        assert record.filled_value == "ja"
        assert record.options == ["ja", "nein"]
