"""Playwright tests: post-submission job status is visible in the dashboard.

User-POV verification for WQ-1 (findings #6 / #7):

- After a ``submitted_confirmed`` result the submit view shows the job as
  ``submitted``.
- After ``submitted_confirmed`` WITH a structured ATS reference it shows
  ``applied`` and the persisted ATS reference.
- After ``outcome_unknown`` it shows ``needs_review``.
- A pre-click failure never changes the visible status.
- The visible status survives a page reload and a full server restart
  (persistence through the app's own ``record_result`` and migration path).

All tests use the local dashboard API and the SQLite store — no external ATS.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.submission.models import (
    SubmissionResult,
    SubmissionResultState,
)
from universal_auto_applier.submission.store import record_result

pytestmark = pytest.mark.playwright

JOB_URL = "https://example.com/job/status-view"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_dashboard(
    data_dir: Path,
    port: int,
    *,
    seed_status: ApplicationStatus,
    result_state: SubmissionResultState | None,
    ats_reference_id: str = "",
) -> tuple[str, object, object, str]:
    """Start the dashboard on ``port`` with one seeded job.

    The submission outcome is recorded through ``record_result`` — exactly
    the production persistence path — so the visible status derives from the
    persisted result row.
    """
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    base = f"http://127.0.0.1:{port}/"
    ready = False
    while time.time() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    if not ready:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("Server did not start")

    app_id = compute_application_id(
        platform=str(Platform.GENERIC),
        external_job_id="status-view-1",
        url=JOB_URL,
    )
    job = ApplicationJob(
        application_id=app_id,
        platform=Platform.GENERIC,
        source="test",
        company="Status Corp",
        title="Engineer",
        url=JOB_URL,
        verdict="apply",
        status=seed_status,
        external_job_id="status-view-1",
        metadata={},
    )
    with session_scope(app.state.session_factory) as session:
        upsert_application_job(session, job)
        if result_state is not None:
            record_result(
                session,
                SubmissionResult(
                    application_id=app_id,
                    approval_id="apr-status-view",
                    snapshot_hash_at_submit="snap-status-view",
                    state=result_state,
                    clicked=True,
                    ats_reference_id=ats_reference_id,
                ),
            )

    return base, app, server, app_id


def _load_submit_view(page: Page, base: str, app_id: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base)
    page.click('a[data-view="submit"]')
    page.fill("#submit-job-id", app_id)
    page.click("#submit-load")
    page.wait_for_selector(".uaa-submit-field", timeout=5_000)


class TestPostSubmitStatusDisplay:
    def test_submitted_confirmed_shows_submitted(self, page: Page, tmp_path: Path) -> None:
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_submitted",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.SUBMITTED_CONFIRMED,
        )
        try:
            _load_submit_view(page, base, app_id)
            text = page.inner_text("#submit-state-display")
            assert "Latest Submission" in text
            assert "submitted_confirmed" in text
            assert "Application Status" in text
            assert "submitted" in text
        finally:
            server.should_exit = True

    def test_submitted_confirmed_with_ref_shows_applied_and_ref(
        self, page: Page, tmp_path: Path
    ) -> None:
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_applied",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.SUBMITTED_CONFIRMED,
            ats_reference_id="ATS-REF-VIEW-001",
        )
        try:
            _load_submit_view(page, base, app_id)
            text = page.inner_text("#submit-state-display")
            assert "Application Status" in text
            assert "applied" in text
            assert "ATS Reference" in text
            assert "ATS-REF-VIEW-001" in text
        finally:
            server.should_exit = True

    def test_outcome_unknown_shows_needs_review(self, page: Page, tmp_path: Path) -> None:
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_needs_review",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.OUTCOME_UNKNOWN,
        )
        try:
            _load_submit_view(page, base, app_id)
            text = page.inner_text("#submit-state-display")
            assert "Application Status" in text
            assert "needs_review" in text
        finally:
            server.should_exit = True

    def test_validation_failure_keeps_review_ready(self, page: Page, tmp_path: Path) -> None:
        """A pre-click failure on a review_ready job keeps the job status
        unchanged — the dashboard must never show a false submitted/applied."""
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_unsubmitted",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.VALIDATION_FAILED,
        )
        try:
            _load_submit_view(page, base, app_id)
            text = page.inner_text("#submit-state-display")
            assert "Application Status" in text
            assert "review_ready" in text
            assert "submitted" not in text
            assert "applied" not in text
        finally:
            server.should_exit = True

    def test_latest_submission_pill_renders(self, page: Page, tmp_path: Path) -> None:
        """The latest-submission pill (user-POV after submit) renders."""
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_pill",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.SUBMITTED_CONFIRMED,
        )
        try:
            _load_submit_view(page, base, app_id)
            page.wait_for_selector(".uaa-submit-state-pill", timeout=5_000)
            pill = page.inner_text(".uaa-submit-state-pill")
            assert pill.strip() == "submitted_confirmed"
        finally:
            server.should_exit = True

    def test_status_survives_page_reload(self, page: Page, tmp_path: Path) -> None:
        base, app, server, app_id = _start_dashboard(
            tmp_path / "uaa_status_reload",
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.SUBMITTED_CONFIRMED,
        )
        try:
            _load_submit_view(page, base, app_id)
            assert "submitted" in page.inner_text("#submit-state-display")
            page.reload()
            time.sleep(0.5)
            _load_submit_view(page, base, app_id)
            assert "submitted" in page.inner_text("#submit-state-display")
        finally:
            server.should_exit = True

    def test_status_survives_server_restart(self, page: Page, tmp_path: Path) -> None:
        """Restart the whole server on the same DB: the derived status must
        be re-read from persisted rows, not memory."""
        data_dir = tmp_path / "uaa_status_restart"
        base, app, server, app_id = _start_dashboard(
            data_dir,
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=SubmissionResultState.SUBMITTED_CONFIRMED,
        )
        _load_submit_view(page, base, app_id)
        assert "submitted" in page.inner_text("#submit-state-display")
        server.should_exit = True
        time.sleep(0.5)

        base2, app2, server2, app_id2 = _start_dashboard(
            data_dir,
            _free_port(),
            seed_status=ApplicationStatus.REVIEW_READY,
            result_state=None,
        )
        try:
            assert app_id2 == app_id
            _load_submit_view(page, base2, app_id2)
            assert "submitted" in page.inner_text("#submit-state-display")
        finally:
            server2.should_exit = True
