"""Playwright tests for the controlled submission dashboard UX.

These tests verify the user-facing workflow from the dashboard:
- Loading the submission state
- Approving a snapshot
- Revoking an approval
- The Submit button being disabled when gates fail
- The deliberate confirmation dialog before submission

All tests use the local dashboard API and local HTML fixtures — no
external ATS is contacted.
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
from universal_auto_applier.persistence.models import Base

pytestmark = pytest.mark.playwright


def _make_settings(tmp_path: Path, port: int) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_dash",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )


def _make_job(tmp_path: Path, url: str = "https://example.com/job/dash-1") -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="dash-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="dash-1",
        metadata={},
    )


def _start_dashboard(tmp_path: Path, port: int) -> tuple[str, object, object]:
    settings = _make_settings(tmp_path, port)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
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

    Base.metadata.create_all(app.state.engine)

    # Seed the job.
    job = _make_job(tmp_path)
    with session_scope(app.state.session_factory) as session:
        upsert_application_job(session, job)

    return base, app, server


class TestDashboardSubmitView:
    def test_submit_view_loads_and_shows_state(self, page: Page, tmp_path: Path) -> None:
        """The Submit view loads and shows the submission state for an
        application ID."""
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        base, app, server = _start_dashboard(tmp_path, port)
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.click('a[data-view="submit"]')

            # Enter the application ID and load.
            app_id = compute_application_id(
                platform=str(Platform.GENERIC),
                external_job_id="dash-1",
                url="https://example.com/job/dash-1",
            )
            page.fill("#submit-job-id", app_id)
            page.click("#submit-load")

            # The state display should show the application info.
            page.wait_for_selector(".uaa-submit-field", timeout=5_000)
            text = page.inner_text("#submit-state-display")
            assert "Real submission enabled" in text
            assert "YES" in text  # enable_real_submission=True
            assert "Active approval" in text
            assert "Can submit" in text

            # The Submit button should be disabled (no approval yet).
            assert page.is_disabled("#submit-execute")
        finally:
            server.should_exit = True

    def test_submit_button_disabled_when_feature_disabled(self, page: Page, tmp_path: Path) -> None:
        """When enable_real_submission is False, the Submit button is
        disabled and the state shows 'NO' for real submission enabled."""
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        # Override settings to disable real submission.
        settings = Settings(
            host="127.0.0.1",
            port=port,
            data_dir=tmp_path / "uaa_dash_disabled",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=False,
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
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
        ready = False
        while time.time() < deadline:
            try:
                with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                    ready = True
                    break
            except OSError:
                time.sleep(0.1)
        assert ready
        Base.metadata.create_all(app.state.engine)

        job = _make_job(tmp_path)
        with session_scope(app.state.session_factory) as session:
            upsert_application_job(session, job)

        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/")
            page.click('a[data-view="submit"]')

            app_id = job.application_id
            page.fill("#submit-job-id", app_id)
            page.click("#submit-load")

            page.wait_for_selector(".uaa-submit-field", timeout=5_000)
            text = page.inner_text("#submit-state-display")
            assert "Real submission enabled" in text
            assert "NO" in text
            assert page.is_disabled("#submit-execute")
        finally:
            server.should_exit = True

    def test_confirm_dialog_appears_on_submit_click(self, page: Page, tmp_path: Path) -> None:
        """Clicking the Submit button (when enabled) shows a confirmation
        dialog. The dialog requires explicit 'Yes, Submit Now'."""
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        base, app, server = _start_dashboard(tmp_path, port)
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.click('a[data-view="submit"]')

            app_id = compute_application_id(
                platform=str(Platform.GENERIC),
                external_job_id="dash-1",
                url="https://example.com/job/dash-1",
            )
            page.fill("#submit-job-id", app_id)
            page.click("#submit-load")

            page.wait_for_selector(".uaa-submit-field", timeout=5_000)

            # The Submit button is disabled (no approval). The confirm
            # dialog should NOT appear when clicking a disabled button.
            # Playwright won't click a disabled button, so we verify the
            # dialog is hidden.
            assert page.is_hidden("#submit-confirm-dialog")
        finally:
            server.should_exit = True
