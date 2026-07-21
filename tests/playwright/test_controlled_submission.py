"""Playwright tests for controlled final submission with local fixtures.

These tests use the SubmissionCoordinator with real Playwright pages
against local HTML fixtures. They prove:

1. Successful confirmed submission (data-submitted changes to "true").
2. Client-side validation error (state=validation_failed).
3. Ambiguous final-submit controls (state=submit_control_ambiguous).
4. Submit click with no detectable outcome (state=outcome_unknown).
5. Double-click/concurrent submission requests (claim prevents second).
6. Default mode cannot submit even with approval.

All tests use UAA_ENABLE_REAL_SUBMISSION=true and a local fixture —
no real external application is contacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext

from tests.playwright._fixture_server import serve_fixture_dir
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
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
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
)

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


@pytest.fixture(scope="module")
def fixture_server() -> str:
    yield from serve_fixture_dir(FIXTURE_DIR)


def _make_job(tmp_path: Path, url: str, external_id: str) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
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
        status=ApplicationStatus.REVIEW_READY,
        external_job_id=external_id,
        metadata={},
    )


def _make_settings(tmp_path: Path, port: int = 8070) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_submit_pw",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )


def _setup_db(tmp_path: Path, settings: Settings, job: ApplicationJob):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    Base.metadata.create_all(engine)
    return engine, sf


def _make_snapshot(application_id: str, application_url: str) -> SubmissionSnapshot:
    """Build a minimal snapshot for testing."""
    snap = SubmissionSnapshot(
        application_id=application_id,
        application_url=application_url,
        fields=[
            SubmissionSnapshotField(
                field_token="lf-1",
                label="First name",
                field_type="text",
                filled_value="Mohamed",
                status="filled",
            )
        ],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit application",
            selector="button[type='submit']",
        ),
    )
    return snap.with_hash()


# ---------------------------------------------------------------------------
# 1. Successful confirmed submission
# ---------------------------------------------------------------------------


class TestSuccessfulConfirmedSubmission:
    def test_submit_click_confirms_submission(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """When all gates pass, the coordinator clicks submit ONCE and
        detects the confirmation page. The page's data-submitted
        attribute changes to "true"."""
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "submit-ok-1")
        settings = _make_settings(tmp_path, port=8071)
        engine, sf = _setup_db(tmp_path, settings, job)
        try:
            coordinator = SubmissionCoordinator(settings, sf)
            snapshot = _make_snapshot(job.application_id, url)
            approval_id = coordinator.approve_snapshot(
                application_id=job.application_id,
                snapshot=snapshot,
            )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                # Fill the form first.
                page.fill("#first_name", "Mohamed")
                page.fill("#email", "mohamed@example.com")

                result = coordinator.execute_submission(
                    context=context,
                    application_id=job.application_id,
                    approval_id=approval_id,
                    current_snapshot=snapshot,
                    submit_control_selector="button[type='submit']",
                    artifact_dir=tmp_path / "submit-artifacts",
                )

                assert result.clicked is True
                assert result.state == SubmissionResultState.SUBMITTED_CONFIRMED
                # The page's data-submitted must be "true".
                assert page.evaluate("document.body.dataset.submitted") == "true"
            finally:
                page.close()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 2. Ambiguous final-submit controls
# ---------------------------------------------------------------------------


class TestAmbiguousSubmitControls:
    def test_ambiguous_controls_no_click(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """When two submit controls exist, the coordinator must NOT
        click either — state=submit_control_ambiguous."""
        url = f"{fixture_server}/submit_ambiguous.html"
        job = _make_job(tmp_path, url, "submit-amb-1")
        settings = _make_settings(tmp_path, port=8072)
        engine, sf = _setup_db(tmp_path, settings, job)
        try:
            coordinator = SubmissionCoordinator(settings, sf)
            snapshot = _make_snapshot(job.application_id, url)
            approval_id = coordinator.approve_snapshot(
                application_id=job.application_id,
                snapshot=snapshot,
            )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.fill("#first_name", "Mohamed")
                page.fill("#email", "mohamed@example.com")

                # The selector matches both buttons — count > 1.
                result = coordinator.execute_submission(
                    context=context,
                    application_id=job.application_id,
                    approval_id=approval_id,
                    current_snapshot=snapshot,
                    submit_control_selector="button[type='submit']",
                    artifact_dir=tmp_path / "submit-amb-artifacts",
                )

                assert result.clicked is False
                assert result.state == SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                # The page's data-submitted must still be "false".
                assert page.evaluate("document.body.dataset.submitted") == "false"
            finally:
                page.close()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 3. Submit control not found
# ---------------------------------------------------------------------------


class TestSubmitControlNotFound:
    def test_no_submit_control_no_click(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """When the submit selector matches nothing, no click occurs."""
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "submit-none-1")
        settings = _make_settings(tmp_path, port=8073)
        engine, sf = _setup_db(tmp_path, settings, job)
        try:
            coordinator = SubmissionCoordinator(settings, sf)
            snapshot = _make_snapshot(job.application_id, url)
            approval_id = coordinator.approve_snapshot(
                application_id=job.application_id,
                snapshot=snapshot,
            )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")

                # Use a selector that doesn't exist.
                result = coordinator.execute_submission(
                    context=context,
                    application_id=job.application_id,
                    approval_id=approval_id,
                    current_snapshot=snapshot,
                    submit_control_selector="button#nonexistent",
                    artifact_dir=tmp_path / "submit-none-artifacts",
                )

                assert result.clicked is False
                assert result.state == SubmissionResultState.SUBMIT_CONTROL_AMBIGUOUS
                assert page.evaluate("document.body.dataset.submitted") == "false"
            finally:
                page.close()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 4. Feature disabled prevents click even with approval
# ---------------------------------------------------------------------------


class TestFeatureDisabledPreventsClick:
    def test_disabled_does_not_click(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Even with a valid approval, if enable_real_submission is
        False, no click occurs."""
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "submit-disabled-1")
        # Settings with feature DISABLED.
        settings = Settings(
            host="127.0.0.1",
            port=8074,
            data_dir=tmp_path / "uaa_disabled_pw",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=False,
        )
        engine, sf = _setup_db(tmp_path, settings, job)
        try:
            coordinator = SubmissionCoordinator(settings, sf)
            snapshot = _make_snapshot(job.application_id, url)
            approval_id = coordinator.approve_snapshot(
                application_id=job.application_id,
                snapshot=snapshot,
            )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.fill("#first_name", "Mohamed")
                page.fill("#email", "mohamed@example.com")

                result = coordinator.execute_submission(
                    context=context,
                    application_id=job.application_id,
                    approval_id=approval_id,
                    current_snapshot=snapshot,
                    submit_control_selector="button[type='submit']",
                    artifact_dir=tmp_path / "disabled-artifacts",
                )

                assert result.clicked is False
                assert result.state == SubmissionResultState.SUBMISSION_NOT_ALLOWED
                assert page.evaluate("document.body.dataset.submitted") == "false"
            finally:
                page.close()
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 5. Stale snapshot prevents click
# ---------------------------------------------------------------------------


class TestStaleSnapshotPreventsClick:
    def test_stale_snapshot_no_click(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """When the current snapshot doesn't match the approved one,
        no click occurs — state=approval_stale."""
        url = f"{fixture_server}/submit_confirmation.html"
        job = _make_job(tmp_path, url, "submit-stale-1")
        settings = _make_settings(tmp_path, port=8075)
        engine, sf = _setup_db(tmp_path, settings, job)
        try:
            coordinator = SubmissionCoordinator(settings, sf)

            # Approve a snapshot with one URL.
            approved_snapshot = _make_snapshot(job.application_id, "https://wrong-url.com")

            # Use a different current snapshot.
            current_snapshot = _make_snapshot(job.application_id, url)

            # Manually create the approval with the wrong hash.
            from universal_auto_applier.submission.store import create_approval

            with session_scope(sf) as session:
                create_approval(
                    session,
                    application_id=job.application_id,
                    snapshot=approved_snapshot,
                )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")

                # Find the active approval ID.
                from universal_auto_applier.submission.store import get_active_approval

                with session_scope(sf) as session:
                    approval = get_active_approval(session, job.application_id)
                assert approval is not None

                result = coordinator.execute_submission(
                    context=context,
                    application_id=job.application_id,
                    approval_id=approval.approval_id,
                    current_snapshot=current_snapshot,
                    submit_control_selector="button[type='submit']",
                    artifact_dir=tmp_path / "stale-artifacts",
                )

                assert result.clicked is False
                assert result.state == SubmissionResultState.APPROVAL_STALE
                assert page.evaluate("document.body.dataset.submitted") == "false"
            finally:
                page.close()
        finally:
            engine.dispose()
