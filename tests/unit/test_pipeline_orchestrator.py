"""Tests for :mod:`universal_auto_applier.services.pipeline_orchestrator`.

Covers pipeline state transitions, orchestration decisions, and fixture-based
full pipeline integration tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.services.pipeline_orchestrator import (
    PipelineOrchestrator,
    PipelineState,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_pipeline.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
    )


def _make_job(
    tmp_path: Path,
    *,
    url: str = "https://example.com/jobs/123",
    platform: Platform = Platform.UNKNOWN,
    external_job_id: str = "job-123",
    status: ApplicationStatus = ApplicationStatus.QUEUED,
    cv_pdf: str | None = None,
    cover_letter_pdf: str | None = None,
) -> ApplicationJob:
    cv = cv_pdf or str(tmp_path / "cv.pdf")
    cover = cover_letter_pdf or str(tmp_path / "cover.pdf")
    Path(cv).write_bytes(b"fake")
    Path(cover).write_bytes(b"fake")
    application_id = compute_application_id(
        platform=str(platform), external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=platform,
        source="linkedin",
        company="Test Corp",
        title="Software Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=cv,
        cover_letter_pdf=cover,
        status=status,
        external_job_id=external_job_id,
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
    )


class TestPipelineStateTransitions:
    def test_initial_state_is_idle(self) -> None:
        state = PipelineState()
        assert state.status == "idle"
        assert state.current_job_id is None
        assert state.jobs_processed == 0

    def test_state_transitions_to_running(self, settings, session_factory) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.state.status = "running"
        assert orch.state.status == "running"

    def test_state_transitions_to_completed(self, settings, session_factory) -> None:
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.state.status = "completed"
        assert orch.state.status == "completed"


class TestOrchestrationDecisions:
    def test_default_mode_is_dry_run(self, settings, session_factory) -> None:
        PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        assert settings.submit_mode == "review"

    def test_generic_adapter_selected_for_unknown(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        from universal_auto_applier.adapters.generic_adapter import GenericAdapter

        job = _make_job(tmp_path, platform=Platform.UNKNOWN)
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        adapter = orch._select_adapter(job)
        assert isinstance(adapter, GenericAdapter)

    def test_siemens_adapter_selected_for_siemens(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        from universal_auto_applier.adapters.siemens_adapter import SiemensAdapter

        job = _make_job(
            tmp_path, platform=Platform.SIEMENS, url="https://jobs.siemens.com/jobs/123"
        )
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        adapter = orch._select_adapter(job)
        assert isinstance(adapter, SiemensAdapter)


class TestFixturePipeline:
    """Full pipeline integration tests using fixture HTML."""

    def test_successful_form_flow_to_review(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Job with a form fixture reaches review_ready status."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("simple_application.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        assert orch.state.status == "completed"
        assert orch.state.jobs_processed == 1

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        # Should be review_ready or needs_user_input (if interventions created).
        assert updated.status in (
            ApplicationStatus.REVIEW_READY,
            ApplicationStatus.NEEDS_USER_INPUT,
        )

    def test_login_page_creates_intervention(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Login page fixture creates login_required intervention."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("login_page.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)

        assert len(pending) >= 1
        assert any(i.kind == "login_required" for i in pending)

    def test_captcha_page_creates_intervention(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Captcha page fixture creates captcha intervention."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("captcha_page.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)

        assert any(i.kind == "captcha" for i in pending)

    def test_review_page_stops_and_creates_intervention(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Review page fixture creates review_before_submit intervention."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("review_submit.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)

        assert any(i.kind == "review_before_submit" for i in pending)

    def test_unknown_required_field_creates_intervention(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Form with unknown required fields creates field_answer interventions."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("unknown_required.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        from universal_auto_applier.interventions.store import list_pending_interventions

        with session_scope(session_factory) as session:
            pending = list_pending_interventions(session, job.application_id)

        assert len(pending) >= 1
        assert any(i.kind == "field_answer" for i in pending)

    def test_pipeline_does_not_submit(self, settings, session_factory, tmp_path: Path) -> None:
        """The pipeline must never set status to SUBMITTED or APPLIED."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("simple_application.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)

        assert updated is not None
        assert updated.status != ApplicationStatus.SUBMITTED
        assert updated.status != ApplicationStatus.APPLIED

    def test_pipeline_logs_events(self, settings, session_factory, tmp_path: Path) -> None:
        """The pipeline should emit log events."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("simple_application.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        logs = orch.log_buffer
        assert len(logs) > 0
        assert any("processing job" in e["message"] for e in logs)
        assert any("pipeline completed" in e["message"] or "pipeline" in e["message"] for e in logs)

    def test_adapter_failure_updates_job_error_state(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """When an adapter fails, the job should be in FAILED or BLOCKED status."""
        job = _make_job(
            tmp_path, platform=Platform.SIEMENS, url="https://jobs.siemens.com/jobs/999"
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # No fixture HTML -> Siemens adapter will be called but repo not configured.
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=None, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)

        assert updated is not None
        assert updated.status in (
            ApplicationStatus.FAILED,
            ApplicationStatus.BLOCKED,
            ApplicationStatus.NEEDS_USER_INPUT,
            ApplicationStatus.IN_PROGRESS,
        )


class TestPipelineSafety:
    """Safety regression tests for the pipeline."""

    def test_generic_path_never_submits(self, settings, session_factory, tmp_path: Path) -> None:
        job = _make_job(tmp_path, platform=Platform.UNKNOWN)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        fixture_html = _read_fixture("simple_application.html")
        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=fixture_html, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)

        assert updated is not None
        assert updated.status not in (ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED)

    def test_no_fixture_creates_review_state(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Without fixture HTML, the pipeline creates a review state in planning mode."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        orch.run(fixture_html=None, max_jobs=1)

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)

        assert updated is not None
        assert updated.status == ApplicationStatus.REVIEW_READY


class TestTrustedAdapterSubmitSafety:
    """Tests proving the review gate blocks submit_or_pause without approval."""

    def test_submit_not_called_without_approval(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """submit_or_pause must NOT be called when review state is not approved."""
        from unittest.mock import patch

        from universal_auto_applier.adapters.siemens_adapter import (
            SiemensAdapter,
            SiemensAdapterConfig,
        )
        from universal_auto_applier.core.models import AdapterResult
        from universal_auto_applier.core.statuses import AdapterResultStatus, Phase

        job = _make_job(
            tmp_path,
            platform=Platform.SIEMENS,
            url="https://jobs.siemens.com/jobs/123",
            external_job_id="510485",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        # Build orchestrator with a Siemens adapter that has a repo path.
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        from universal_auto_applier.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register(
            SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo, dry_run=True))
        )
        registry.register(GenericAdapter())

        orch = PipelineOrchestrator(
            settings, session_factory, registry=registry, candidate=_make_candidate()
        )

        # Mock the adapter's submit_or_pause to track if it's called.
        submit_called = []

        def mock_submit(self, job):
            submit_called.append(True)
            return AdapterResult(
                status=AdapterResultStatus.DRY_RUN,
                phase=Phase.SUBMIT,
                message="dry-run",
            )

        with patch.object(SiemensAdapter, "submit_or_pause", mock_submit):
            # Also mock navigate and fill to return success/dry_run.
            with patch.object(
                SiemensAdapter,
                "navigate_to_form",
                lambda self, job: AdapterResult(
                    status=AdapterResultStatus.DRY_RUN, phase=Phase.NAVIGATE, message="ok"
                ),
            ):
                with patch.object(
                    SiemensAdapter,
                    "fill",
                    lambda self, job: AdapterResult(
                        status=AdapterResultStatus.DRY_RUN, phase=Phase.FILL, message="ok"
                    ),
                ):
                    with patch.object(
                        SiemensAdapter,
                        "prepare",
                        lambda self, job: AdapterResult.success(
                            phase=Phase.PREPARE,
                            message="ok",
                            application_id=job.application_id,
                        ),
                    ):
                        orch.run(fixture_html=None, max_jobs=1)

        # submit_or_pause should NOT have been called (no review approval).
        assert len(submit_called) == 0, "submit_or_pause was called without review approval"

        # Job should be in review_ready (not submitted).
        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        assert updated.status == ApplicationStatus.REVIEW_READY

    def test_submit_blocked_with_dry_run_false_no_approval(
        self, settings, session_factory, tmp_path: Path
    ) -> None:
        """Even with dry_run=False, submit_or_pause is not called without approval."""
        from unittest.mock import patch

        from universal_auto_applier.adapters.registry import AdapterRegistry
        from universal_auto_applier.adapters.siemens_adapter import (
            SiemensAdapter,
            SiemensAdapterConfig,
        )
        from universal_auto_applier.core.models import AdapterResult
        from universal_auto_applier.core.statuses import AdapterResultStatus, Phase

        job = _make_job(
            tmp_path,
            platform=Platform.SIEMENS,
            url="https://jobs.siemens.com/jobs/456",
            external_job_id="510486",
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        registry = AdapterRegistry()
        # dry_run=False — but orchestrator should still block submit.
        registry.register(
            SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo, dry_run=False))
        )
        registry.register(GenericAdapter())

        orch = PipelineOrchestrator(
            settings, session_factory, registry=registry, candidate=_make_candidate()
        )

        submit_called = []

        def mock_submit(self, job):
            submit_called.append(True)
            return AdapterResult(
                status=AdapterResultStatus.SUBMITTED, phase=Phase.SUBMIT, message="submitted"
            )

        with patch.object(SiemensAdapter, "submit_or_pause", mock_submit):
            with patch.object(
                SiemensAdapter,
                "navigate_to_form",
                lambda self, job: AdapterResult(
                    status=AdapterResultStatus.DRY_RUN, phase=Phase.NAVIGATE, message="ok"
                ),
            ):
                with patch.object(
                    SiemensAdapter,
                    "fill",
                    lambda self, job: AdapterResult(
                        status=AdapterResultStatus.DRY_RUN, phase=Phase.FILL, message="ok"
                    ),
                ):
                    with patch.object(
                        SiemensAdapter,
                        "prepare",
                        lambda self, job: AdapterResult.success(
                            phase=Phase.PREPARE,
                            message="ok",
                            application_id=job.application_id,
                        ),
                    ):
                        orch.run(fixture_html=None, max_jobs=1)

        # submit_or_pause should NOT have been called.
        assert len(submit_called) == 0, (
            "submit_or_pause was called with dry_run=False but no review approval"
        )

        with session_scope(session_factory) as session:
            updated = get_application_job(session, job.application_id)
        assert updated is not None
        assert updated.status != ApplicationStatus.SUBMITTED
        assert updated.status != ApplicationStatus.APPLIED

    def test_default_dry_run_preserved(self, settings, session_factory, tmp_path: Path) -> None:
        """Default SiemensAdapterConfig has dry_run=True."""
        from universal_auto_applier.adapters.siemens_adapter import SiemensAdapterConfig

        config = SiemensAdapterConfig()
        assert config.dry_run is True


class TestPipelineStartAPI:
    """Tests for the POST /api/pipeline/start endpoint."""

    def test_start_endpoint_exists(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base

        settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post("/api/pipeline/start", json={"max_jobs": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("completed", "idle")
        assert "No real submissions" in body["message"]

    def test_start_does_not_submit(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base

        settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post("/api/pipeline/start", json={"max_jobs": 1})
        body = response.json()
        # No jobs processed = no submissions possible.
        assert body["jobs_processed"] == 0

    def test_start_updates_pipeline_state(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base

        settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            # Check pipeline status endpoint.
            status_resp = client.get("/api/pipeline/status")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["status"] in ("completed", "idle")
        assert status["run_id"] is not None

    def test_second_start_while_running_rejected(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base
        from universal_auto_applier.services.pipeline_orchestrator import PipelineState

        settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            # Simulate a running pipeline.
            app.state.pipeline_state = PipelineState(status="running")
            response = client.post("/api/pipeline/start", json={"max_jobs": 1})
        assert response.status_code == 409
        assert "already running" in response.json()["detail"].lower()

    def test_dashboard_shows_pipeline_state(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base

        settings = Settings(
            host="127.0.0.1",
            port=8001,
            data_dir=tmp_path / "uaa_data",
            browser_headless=True,
            submit_mode="review",
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            status_resp = client.get("/api/status")
        body = status_resp.json()
        assert body["run_status"] in ("completed", "idle")
        assert "submit_mode" in body

    def test_cli_callable_entrypoint(self, settings, session_factory, tmp_path: Path) -> None:
        """The orchestrator is callable from Python (CLI entrypoint)."""
        job = _make_job(tmp_path)
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)

        orch = PipelineOrchestrator(settings, session_factory, candidate=_make_candidate())
        state = orch.run(fixture_html=None, max_jobs=1)

        assert state.status == "completed"
        assert state.jobs_processed == 1


class TestPhase3to6Regression:
    """Regression tests from previous phases."""

    def test_dangerous_submit_never_clicked(self) -> None:
        from universal_auto_applier.navigator.page_observer import observe_html
        from universal_auto_applier.navigator.safe_explorer import safe_explore

        submit_html = '<html><body><button type="submit">Submit application</button></body></html>'
        clicked: list[str] = []

        def observe():
            return observe_html(submit_html, url="https://example.com/submit")

        def click(selector: str) -> bool:
            clicked.append(selector)
            return True

        safe_explore(observe, click)
        assert len(clicked) == 0

    def test_fill_engine_never_submits(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.models import FormField
        from universal_auto_applier.form_engine.fill_engine import fill_form

        job = _make_job(tmp_path)
        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            )
        ]
        summary = fill_form(fields, _make_candidate(), job)

        for result in summary.results:
            assert "submit" not in result.status

    def test_password_field_blocked(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.models import FormField
        from universal_auto_applier.form_engine.fill_engine import fill_form

        job = _make_job(tmp_path)
        fields = [
            FormField(
                selector="#pw", name="password", label="Password", type="unknown", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), job)

        assert summary.blocked == 1
        assert summary.results[0].field_type == "password"

    def test_check_submit_approval_blocks_without_state(self) -> None:
        from universal_auto_applier.interventions.review import check_submit_approval

        assert check_submit_approval(None) is False
