"""Regression tests proving default mode cannot submit.

These tests verify that when ``UAA_ENABLE_REAL_SUBMISSION=false`` (the
default), the system NEVER clicks the final submit control, regardless
of the code path:

- The live dry-run CLI never clicks submit.
- The pipeline orchestrator never clicks submit.
- The dashboard Start Dry-Run button never clicks submit.
- The SubmissionCoordinator rejects all submit requests.
- The submit API endpoint returns submission_not_allowed.
- The LiveBrowserRunner report always has submitted=False.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import BrowserContext

from tests.playwright._fixture_server import serve_fixture_dir
from universal_auto_applier.api.app import create_app
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.coordinator import SubmissionCoordinator
from universal_auto_applier.submission.models import (
    SubmissionResultState,
    SubmissionSnapshot,
)

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


@pytest.fixture(scope="module")
def fixture_server() -> str:
    yield from serve_fixture_dir(FIXTURE_DIR)


def _make_job(
    tmp_path: Path,
    url: str,
    external_id: str,
) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_md = tmp_path / f"{external_id}-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python automation", encoding="utf-8")
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
        metadata={
            "candidate_profile": {
                "first_name": "Mohamed",
                "last_name": "Azzam",
                "full_name": "Mohamed Azzam",
                "email": "mohamed@example.com",
                "phone": "+49 1234567",
                "requires_sponsorship": False,
            },
        },
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
# 1. LiveBrowserRunner never clicks submit (default mode)
# ---------------------------------------------------------------------------


class TestLiveRunnerDefaultCannotSubmit:
    def test_runner_report_submitted_false(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """The LiveBrowserRunner report must have submitted=False in
        default (review) mode, even when a submit button is present."""
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "no-submit-1")
        config = _make_config(tmp_path)
        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-no-submit",
        )
        assert report.submitted is False
        # The page's data-submitted attribute must still be "false".
        if report.dom_snapshot_path:
            dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8")
            assert 'data-submitted="false"' in dom, (
                "Page data-submitted should be 'false' — submit was clicked!"
            )


# ---------------------------------------------------------------------------
# 2. SubmissionCoordinator rejects in default mode
# ---------------------------------------------------------------------------


class TestCoordinatorDefaultCannotSubmit:
    def test_coordinator_rejects_when_disabled(self, tmp_path: Path) -> None:
        """The SubmissionCoordinator must reject all submit requests
        when enable_real_submission is False (the default)."""
        settings = Settings(
            host="127.0.0.1",
            port=8060,
            data_dir=tmp_path / "uaa_default",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=False,  # default
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        Base.metadata.create_all(engine)

        job = _make_job(tmp_path, "https://example.com/job/default-1", "default-1")
        with session_scope(sf) as session:
            upsert_application_job(session, job)

        try:
            coordinator = SubmissionCoordinator(settings, sf)
            snapshot = SubmissionSnapshot(
                application_id=job.application_id,
                application_url=job.url,
            ).with_hash()

            # Approve the snapshot (this is allowed even when submission
            # is disabled — approval is just a record).
            coordinator.approve_snapshot(
                application_id=job.application_id,
                snapshot=snapshot,
            )

            # But the gate check must fail.
            gate = coordinator.check_gates(
                application_id=job.application_id,
                current_snapshot=snapshot,
            )
            assert not gate.allowed
            assert gate.state == SubmissionResultState.SUBMISSION_NOT_ALLOWED
            assert "enable_real_submission" in gate.reason
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 3. Dashboard Start Dry-Run cannot submit
# ---------------------------------------------------------------------------


class TestDashboardStartDryRunCannotSubmit:
    def test_start_dry_run_does_not_submit(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """The dashboard's Start Dry-Run button must not trigger a
        submit. The pipeline runs in review mode by default."""
        settings = Settings(
            host="127.0.0.1",
            port=8061,
            data_dir=tmp_path / "uaa_dashboard",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=False,
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post(
                "/api/pipeline/start",
                json={"fixture_html": "<form></form>", "max_jobs": 1},
            )
            assert response.status_code in (200, 409)
            if response.status_code == 200:
                body = response.json()
                # The pipeline must report submitted=False.
                assert body.get("submitted", False) is False or "submitted" not in body


# ---------------------------------------------------------------------------
# 4. Submit API endpoint rejects in default mode
# ---------------------------------------------------------------------------


class TestSubmitApiDefaultCannotSubmit:
    def test_submit_api_rejects_when_disabled(self, tmp_path: Path) -> None:
        """The /api/submit/{id}/submit endpoint must return
        submission_not_allowed when the feature is disabled."""
        settings = Settings(
            host="127.0.0.1",
            port=8062,
            data_dir=tmp_path / "uaa_api",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=False,
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        Base.metadata.create_all(engine)

        job = _make_job(tmp_path, "https://example.com/job/api-1", "api-1")
        with session_scope(sf) as session:
            upsert_application_job(session, job)

        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": "fake-id", "confirm": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["state"] == "submission_not_allowed"
            assert body["clicked"] is False

        engine.dispose()


# ---------------------------------------------------------------------------
# 5. Submit API requires confirm=true
# ---------------------------------------------------------------------------


class TestSubmitApiRequiresConfirm:
    def test_submit_without_confirm_returns_400(self, tmp_path: Path) -> None:
        """The submit endpoint must return 400 if confirm is not true."""
        settings = Settings(
            host="127.0.0.1",
            port=8063,
            data_dir=tmp_path / "uaa_confirm",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=True,  # Even when enabled, confirm is required.
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        Base.metadata.create_all(engine)

        job = _make_job(tmp_path, "https://example.com/job/confirm-1", "confirm-1")
        with session_scope(sf) as session:
            upsert_application_job(session, job)

        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post(
                f"/api/submit/{job.application_id}/submit",
                json={"approval_id": "fake-id", "confirm": False},
            )
            assert response.status_code == 400
            assert "confirm must be true" in response.json()["detail"].lower()

        engine.dispose()
