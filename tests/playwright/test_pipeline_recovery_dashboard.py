"""Playwright test for WQ-5 recovered run display on the dashboard.

User-perspective proof:
- Seed a proven-stale active run (dead worker pid + expired heartbeat) and an IN_PROGRESS job.
- Start the application (uvicorn server thread) so startup recovery executes.
- Open the dashboard as a user via Playwright.
- Prove the pipeline status visibly renders `recovered`.
- Prove the recovered/interrupted reason or safe review guidance is visible.
- Prove the display is not styled as active/running (controls correct, no active pill styling).
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
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
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.pipeline_run_repository import (
    create_pipeline_run,
    update_pipeline_run,
)

pytestmark = pytest.mark.playwright


def _dead_pid() -> int:
    """Return a pid that is guaranteed to no longer exist."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def _old() -> datetime:
    """Return a timestamp far older than the 30s heartbeat timeout."""
    return datetime.now(UTC) - timedelta(minutes=10)


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_dashboard_visibly_renders_recovered_run_and_guidance(tmp_path: Path, page: Page) -> None:
    """Prove to the user that a recovered stale run renders visibly as 'recovered',
    displays the recovery guidance/reason, and is not styled as active/running."""
    data_dir = tmp_path / "uaa_recovered_dashboard"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "uaa.sqlite"
    url = build_engine_url(db_path)
    apply_migrations(url)

    engine = make_engine(url)
    factory = make_session_factory(engine)

    job_id = compute_application_id(
        platform="greenhouse",
        external_job_id="stale-ui-job-1",
        url="https://example.com/jobs/stale-ui-job-1",
    )
    job = ApplicationJob(
        application_id=job_id,
        platform=Platform.GREENHOUSE,
        source="test",
        company="Stale Corp",
        title="Software Engineer",
        url="https://example.com/jobs/stale-ui-job-1",
        verdict="apply",
        status=ApplicationStatus.IN_PROGRESS,
        external_job_id="stale-ui-job-1",
    )

    with session_scope(factory) as session:
        upsert_application_job(session, job)
        create_pipeline_run(
            session, run_id="run-stale-ui-1", status="running", mode="sequential_dry_run"
        )
        update_pipeline_run(
            session,
            "run-stale-ui-1",
            current_job_id=job_id,
            current_phase="orchestrate",
            worker_pid=_dead_pid(),
            worker_started_at=_old(),
            heartbeat_at=_old(),
            jobs_total=1,
            jobs_completed=0,
            jobs_failed=0,
        )
    engine.dispose()

    port = _free_port()
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=data_dir,
        browser_headless=True,
        submit_mode="review",
    )

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

    assert ready, "Dashboard server failed to start"

    try:
        server_url = f"http://127.0.0.1:{port}/"
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(server_url)

        # 1. Pipeline status visibly renders "recovered"
        page.wait_for_selector("#run-status", timeout=10_000)
        run_status_text = page.locator("#run-status").inner_text()
        assert "recovered" in run_status_text.lower()

        # 2. Display is NOT styled as active/running
        run_status_class = page.locator("#run-status").get_attribute("class") or ""
        assert "uaa-pill-running" not in run_status_class

        # Controls verify non-active state: start enabled, pause/resume/cancel disabled
        assert page.locator("#pipeline-start").is_enabled()
        assert page.locator("#pipeline-pause").is_disabled()
        assert page.locator("#pipeline-resume").is_disabled()
        assert page.locator("#pipeline-cancel").is_disabled()

        # 3. Interrupted reason or safe review guidance is visible
        page.wait_for_selector("#pipeline-last-error", timeout=5_000)
        last_error_text = page.locator("#pipeline-last-error").inner_text()
        assert (
            "interrupted" in last_error_text.lower()
            or "review" in last_error_text.lower()
            or "recovered" in last_error_text.lower()
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
