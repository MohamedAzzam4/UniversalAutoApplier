"""Tests for CLI wiring and intervention persistence.

Proves:
- The CLI live-dry-run path creates and passes a qa_service to the runner.
- When LLM is configured, the run uses LLM-assisted mode.
- When LLM is unconfigured, deterministic-only mode continues.
- Interventions are persisted for unresolved/confirmation-required fields.
- Reprocessing does not create duplicate interventions.
- The correct application owns the interventions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from universal_auto_applier.browser.live_models import (
    LiveFieldRecord,
    LiveRunReport,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.interventions.store import list_pending_interventions
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.models import Base


def _make_job(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    url = "https://boards.greenhouse.io/example/jobs/cli-1"
    application_id = compute_application_id(platform="greenhouse", external_job_id="cli-1", url=url)
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="CLI Corp",
        title="Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.QUEUED,
        external_job_id="cli-1",
        metadata={"candidate_profile": {"first_name": "Test", "email": "test@example.com"}},
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
    )


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_cli.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


# ---------------------------------------------------------------------------
# CLI wiring tests
# ---------------------------------------------------------------------------


class TestCLIWiring:
    def test_cli_creates_qa_service(self, settings, tmp_path: Path) -> None:
        """The CLI _live_dry_run creates a qa_service and passes it to the runner."""
        from universal_auto_applier.cli import _live_dry_run
        from universal_auto_applier.persistence.db import build_engine_url
        from universal_auto_applier.persistence.job_repository import upsert_application_job
        from universal_auto_applier.persistence.migrations import apply_migrations

        # Set up DB with a job.
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        job = _make_job(tmp_path)
        from universal_auto_applier.persistence.db import make_engine, make_session_factory

        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        # Mock the runner so we can capture the qa_service argument.
        captured_args: dict = {}

        def mock_run(self, job, candidate, qa_service=None):
            captured_args["qa_service"] = qa_service
            captured_args["job"] = job
            from datetime import datetime

            return LiveRunReport(
                application_id=job.application_id,
                status="review_ready",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                initial_url=job.url,
                submitted=False,
            )

        # Mock create_qa_service to return a configured mock.
        mock_service = MagicMock()
        mock_service.is_configured = True

        with patch("universal_auto_applier.browser.live_runner.LiveBrowserRunner.run", mock_run):
            with patch(
                "universal_auto_applier.llm.qa_service.create_qa_service",
                return_value=mock_service,
            ):
                args = MagicMock()
                args.application_id = job.application_id[:12]
                args.start_url = None
                args.artifacts_dir = None
                args.profile_dir = None
                args.ephemeral_profile = True
                args.headless = True
                args.channel = None
                args.timeout_ms = None
                args.max_steps = None
                exit_code = _live_dry_run(settings, args)

        assert exit_code == 0
        # The qa_service was passed to the runner.
        assert captured_args["qa_service"] is mock_service

    def test_cli_passes_none_when_unconfigured(self, settings, tmp_path: Path) -> None:
        """When LLM is not configured, qa_service=None is passed (deterministic-only)."""
        from universal_auto_applier.cli import _live_dry_run
        from universal_auto_applier.persistence.db import (
            build_engine_url,
            make_engine,
            make_session_factory,
        )
        from universal_auto_applier.persistence.job_repository import upsert_application_job
        from universal_auto_applier.persistence.migrations import apply_migrations

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job)
        engine.dispose()

        captured_args: dict = {}

        def mock_run(self, job, candidate, qa_service=None):
            captured_args["qa_service"] = qa_service
            from datetime import datetime

            return LiveRunReport(
                application_id=job.application_id,
                status="review_ready",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                initial_url=job.url,
                submitted=False,
            )

        # Mock create_qa_service to return an unconfigured mock.
        mock_service = MagicMock()
        mock_service.is_configured = False

        with patch("universal_auto_applier.browser.live_runner.LiveBrowserRunner.run", mock_run):
            with patch(
                "universal_auto_applier.llm.qa_service.create_qa_service",
                return_value=mock_service,
            ):
                args = MagicMock()
                args.application_id = job.application_id[:12]
                args.start_url = None
                args.artifacts_dir = None
                args.profile_dir = None
                args.ephemeral_profile = True
                args.headless = True
                args.channel = None
                args.timeout_ms = None
                args.max_steps = None
                _live_dry_run(settings, args)

        # When unconfigured, qa_service should be None.
        assert captured_args["qa_service"] is None


# ---------------------------------------------------------------------------
# Intervention persistence tests
# ---------------------------------------------------------------------------


class TestInterventionPersistence:
    def _seed_job(self, session_factory, external_job_id: str, tmp_path: Path) -> str:
        """Seed a job so the FK constraint on interventions is satisfied.

        Returns the application_id that was computed from the external_job_id.
        """
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        cv = tmp_path / f"{external_job_id}-cv.pdf"
        cover = tmp_path / f"{external_job_id}-cover.pdf"
        cv.write_bytes(b"%PDF fake")
        cover.write_bytes(b"%PDF fake")
        url = f"https://boards.greenhouse.io/example/jobs/{external_job_id}"
        application_id = compute_application_id(
            platform="greenhouse", external_job_id=external_job_id, url=url
        )
        job = ApplicationJob(
            application_id=application_id,
            platform=Platform.GREENHOUSE,
            source="linkedin",
            company="Test",
            title="Eng",
            url=url,
            score=4.0,
            verdict="apply",
            cv_pdf=str(cv),
            cover_letter_pdf=str(cover),
            status=ApplicationStatus.QUEUED,
            external_job_id=external_job_id,
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
        return application_id

    def test_intervention_persisted_for_unresolved_field(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """An intervention is persisted for each unresolved field."""
        from datetime import datetime

        from universal_auto_applier.cli import _persist_interventions

        application_id = self._seed_job(session_factory, "persist-1", tmp_path)

        report = LiveRunReport(
            application_id=application_id,
            status="needs_user_input",
            started_at=datetime.now(),
            initial_url="https://example.com",
            submitted=False,
            fields=[
                LiveFieldRecord(
                    page_url="https://example.com",
                    selector="input[name='salary']",
                    label="What is your salary expectation?",
                    field_type="text",
                    status="intervention_needed",
                    field_token="live-field-0-3",
                    proposed_answer="50000",
                    confidence=0.7,
                    evidence_summary="Some evidence",
                    category="salary",
                    risk_level="high",
                    requires_confirmation=True,
                ),
            ],
        )

        engine = session_factory.kw["bind"]  # type: ignore[attr-defined]
        with patch(
            "universal_auto_applier.cli._open_store", return_value=(engine, session_factory)
        ):
            _persist_interventions(settings, application_id, report)

        with session_scope(session_factory) as session:
            interventions = list_pending_interventions(session, application_id)

        assert len(interventions) >= 1
        iv = interventions[0]
        assert iv.application_id == application_id
        assert "salary" in iv.question.lower()
        assert iv.suggested_answer == "50000"
        assert iv.confidence == 0.7
        assert iv.field_selector == "live-field-0-3"

    def test_reprocessing_does_not_create_duplicates(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Reprocessing the same form does not create duplicate interventions."""
        from datetime import datetime

        from universal_auto_applier.cli import _persist_interventions

        application_id = self._seed_job(session_factory, "dedup-1", tmp_path)

        report = LiveRunReport(
            application_id=application_id,
            status="needs_user_input",
            started_at=datetime.now(),
            initial_url="https://example.com",
            submitted=False,
            fields=[
                LiveFieldRecord(
                    page_url="https://example.com",
                    selector="input[name='q']",
                    label="Experience with Python?",
                    field_type="radio",
                    status="intervention_needed",
                    field_token="live-field-0-1",
                    proposed_answer="Yes",
                    confidence=0.6,
                    requires_confirmation=True,
                ),
            ],
        )

        engine = session_factory.kw["bind"]  # type: ignore[attr-defined]
        with patch(
            "universal_auto_applier.cli._open_store", return_value=(engine, session_factory)
        ):
            _persist_interventions(settings, application_id, report)
            _persist_interventions(settings, application_id, report)

        with session_scope(session_factory) as session:
            interventions = list_pending_interventions(session, application_id)

        assert len(interventions) == 1

    def test_correct_application_owns_intervention(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """The intervention is associated with the correct application_id."""
        from datetime import datetime

        from universal_auto_applier.cli import _persist_interventions

        app_a = self._seed_job(session_factory, "own-a", tmp_path)
        app_b = self._seed_job(session_factory, "own-b", tmp_path)

        for app_id in (app_a, app_b):
            report = LiveRunReport(
                application_id=app_id,
                status="needs_user_input",
                started_at=datetime.now(),
                initial_url="https://example.com",
                submitted=False,
                fields=[
                    LiveFieldRecord(
                        page_url="https://example.com",
                        selector="input[name='q']",
                        label="Question for " + app_id[:8],
                        field_type="text",
                        status="intervention_needed",
                        field_token="live-field-0-0",
                        requires_confirmation=True,
                    ),
                ],
            )
            engine = session_factory.kw["bind"]  # type: ignore[attr-defined]
            with patch(
                "universal_auto_applier.cli._open_store", return_value=(engine, session_factory)
            ):
                _persist_interventions(settings, app_id, report)

        with session_scope(session_factory) as session:
            ivs_a = list_pending_interventions(session, app_a)
            ivs_b = list_pending_interventions(session, app_b)

        assert len(ivs_a) == 1
        assert len(ivs_b) == 1
        assert ivs_a[0].application_id == app_a
        assert ivs_b[0].application_id == app_b

    def test_no_intervention_for_filled_fields(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Fields with status=filled do NOT create interventions."""
        from datetime import datetime

        from universal_auto_applier.cli import _persist_interventions

        application_id = self._seed_job(session_factory, "no-fill-1", tmp_path)

        report = LiveRunReport(
            application_id=application_id,
            status="review_ready",
            started_at=datetime.now(),
            initial_url="https://example.com",
            submitted=False,
            fields=[
                LiveFieldRecord(
                    page_url="https://example.com",
                    selector="input[name='email']",
                    label="Email",
                    field_type="email",
                    status="filled",
                    field_token="live-field-0-0",
                ),
                LiveFieldRecord(
                    page_url="https://example.com",
                    selector="input[name='q']",
                    label="Experience with Python?",
                    field_type="radio",
                    status="intervention_needed",
                    field_token="live-field-0-1",
                    proposed_answer="Yes",
                    confidence=0.6,
                    requires_confirmation=True,
                ),
            ],
        )

        engine = session_factory.kw["bind"]  # type: ignore[attr-defined]
        with patch(
            "universal_auto_applier.cli._open_store", return_value=(engine, session_factory)
        ):
            _persist_interventions(settings, application_id, report)

        with session_scope(session_factory) as session:
            interventions = list_pending_interventions(session, application_id)

        assert len(interventions) == 1
        assert "Python" in interventions[0].question


# ---------------------------------------------------------------------------
# Dashboard API visibility tests
# ---------------------------------------------------------------------------


class TestDashboardApiVisibility:
    def test_persisted_interventions_visible_via_api(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Persisted LLM interventions are returned by GET /api/interventions."""

        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        application_id = compute_application_id(
            platform="greenhouse",
            external_job_id="api-vis-1",
            url="https://boards.greenhouse.io/example/jobs/api-vis-1",
        )
        job = _make_job(tmp_path)
        job = job.model_copy(
            update={
                "application_id": application_id,
                "external_job_id": "api-vis-1",
                "url": "https://boards.greenhouse.io/example/jobs/api-vis-1",
            }
        )

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="What is your salary expectation?",
                    suggested_answer="50000",
                    confidence=0.7,
                    field_selector="live-field-0-3",
                )

            response = client.get("/api/interventions")
            assert response.status_code == 200
            body = response.json()
            assert body["total"] >= 1
            iv = body["interventions"][0]
            assert iv["application_id"] == application_id
            assert "salary" in iv["question"].lower()
            assert iv["suggested_answer"] == "50000"
            assert iv["confidence"] == 0.7
            assert iv["field_selector"] == "live-field-0-3"

    def test_accept_intervention_via_api(self, settings, tmp_path: Path) -> None:
        """Accept (approve) an intervention via POST /api/interventions/{id}/resolve."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        job = _make_job(tmp_path)
        application_id = job.application_id
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                row = create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Experience with Python?",
                    suggested_answer="Yes",
                    confidence=0.9,
                    field_selector="live-field-0-1",
                )
                intervention_id = row.intervention_id

            response = client.post(
                f"/api/interventions/{intervention_id}/resolve",
                json={"resolution": "approved", "answer": "Yes", "save_to_memory": True},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "resolved"

    def test_reject_intervention_via_api(self, settings, tmp_path: Path) -> None:
        """Reject an intervention via POST /api/interventions/{id}/resolve."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        job = _make_job(tmp_path)
        application_id = job.application_id
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                row = create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Salary?",
                    suggested_answer="50000",
                    confidence=0.7,
                    field_selector="live-field-0-2",
                )
                intervention_id = row.intervention_id

            response = client.post(
                f"/api/interventions/{intervention_id}/resolve",
                json={"resolution": "blocked", "save_to_memory": False},
            )
            assert response.status_code == 200

    def test_edit_intervention_via_api(self, settings, tmp_path: Path) -> None:
        """Edit (change the answer of) an intervention via POST /api/interventions/{id}/resolve."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention, get_intervention
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        job = _make_job(tmp_path)
        application_id = job.application_id
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                row = create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Experience with Rust?",
                    suggested_answer="No",
                    confidence=0.5,
                    field_selector="live-field-0-4",
                )
                intervention_id = row.intervention_id

            response = client.post(
                f"/api/interventions/{intervention_id}/resolve",
                json={"resolution": "edited", "answer": "Yes", "save_to_memory": True},
            )
            assert response.status_code == 200

            with session_scope(sf) as session:
                iv = get_intervention(session, intervention_id)
            assert iv is not None
            assert iv.suggested_answer == "Yes"

    def test_remember_answer_via_api(self, settings, tmp_path: Path) -> None:
        """Remember an approved answer so it's reused in future applications."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.answer_memory import retrieve_answer
        from universal_auto_applier.interventions.store import create_intervention
        from universal_auto_applier.persistence.job_repository import upsert_application_job

        job = _make_job(tmp_path)
        application_id = job.application_id
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                row = create_intervention(
                    session,
                    application_id=application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Are you willing to relocate?",
                    suggested_answer="Yes",
                    confidence=1.0,
                    field_selector="live-field-0-5",
                )
                intervention_id = row.intervention_id

            response = client.post(
                f"/api/interventions/{intervention_id}/resolve",
                json={"resolution": "approved", "answer": "Yes", "save_to_memory": True},
            )
            assert response.status_code == 200

            with session_scope(sf) as session:
                memory = retrieve_answer(session, "Are you willing to relocate?")
            assert memory is not None
            assert memory.answer == "Yes"
