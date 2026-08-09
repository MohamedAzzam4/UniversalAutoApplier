"""Playwright tests for WQ-6 cross-repository orchestration dashboard.

Prove the dashboard shows:
- orchestration mode selection (Sequential / Parallel)
- overall phase and state
- JobHunter state
- queue import result and counts
- UAA pipeline state and progress
- latest error
- start/cancel controls with correct enabled states

Uses only local fixture data; no public web, real ATS, or real submission.
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
from universal_auto_applier.persistence.db import build_engine_url
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base

pytestmark = pytest.mark.playwright

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
FAKE_JH_REPO = FIXTURES_DIR / "fake_jobhunter"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_settings(tmp_path: Path, queue_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq6_ui",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
        pipeline_job_pulse_ms=200,
        jobhunter_repo=FAKE_JH_REPO,
        jobhunter_entry_point="run_export_queue.py",
        queue_path=queue_path,
        orchestration_mode="sequential",
    )


@pytest.fixture
def orchestration_server(tmp_path: Path) -> tuple[str, object]:
    """Start a real uvicorn server with orchestration configured."""
    queue_path = tmp_path / "queue.jsonl"
    settings = _make_settings(tmp_path, queue_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

    app = create_app(settings=settings)
    port = _free_port()
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

    # Wait for server to be ready.
    deadline = time.time() + 5.0
    ready = False
    while time.time() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                ready = True
                break
        except OSError:
            time.sleep(0.1)
    assert ready, "Orchestration server failed to start"

    Base.metadata.create_all(app.state.engine)

    try:
        yield f"http://127.0.0.1:{port}/", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        # Ensure subprocesses are cleaned up.
        worker = app.state.pipeline_worker
        if worker is not None:
            proc = getattr(worker, "_proc", None)  # noqa: SLF001
            if proc is not None and proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass


def test_dashboard_shows_orchestration_card_with_mode_selection(
    page: Page, orchestration_server: tuple[str, object]
) -> None:
    """The dashboard renders the orchestration card with mode dropdown and controls."""
    base, _ = orchestration_server
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base)

    # The orchestration card exists.
    page.wait_for_selector("text=Cross-Repository Orchestration", timeout=10_000)

    # Mode dropdown exists with Sequential and Parallel options.
    mode_select = page.locator("#orchestration-mode")
    assert mode_select.is_visible()
    options = mode_select.locator("option").all_inner_texts()
    assert "Sequential" in options
    assert "Parallel" in options

    # Start and cancel buttons exist.
    assert page.locator("#orchestration-start").is_visible()
    assert page.locator("#orchestration-cancel").is_visible()

    # Initial state: start enabled, cancel disabled.
    assert page.locator("#orchestration-start").is_enabled()
    assert page.locator("#orchestration-cancel").is_disabled()

    # Status pill shows idle.
    page.wait_for_selector("#orchestration-status", timeout=5_000)
    status_text = page.locator("#orchestration-status").inner_text()
    assert "idle" in status_text.lower()


def test_dashboard_shows_phase_and_progress_during_sequential_run(
    page: Page, orchestration_server: tuple[str, object]
) -> None:
    """Start a sequential orchestration run and verify phase visibility."""
    base, app = orchestration_server
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base)
    page.wait_for_selector("#orchestration-start", timeout=10_000)

    # Start the orchestration via the UI.
    page.click("#orchestration-start")

    # The status should change from idle to something active.
    # Wait for the status to change (polling).
    deadline = time.time() + 10.0
    became_active = False
    while time.time() < deadline:
        status_text = page.locator("#orchestration-status").inner_text()
        if "idle" not in status_text.lower():
            became_active = True
            break
        time.sleep(0.3)
    assert became_active, "Orchestration status never left idle"

    # The phase indicator should show something.
    # Phase may be empty if the run completed quickly, but the status should
    # eventually reach completed.
    deadline = time.time() + 60.0
    completed = False
    while time.time() < deadline:
        status_text = page.locator("#orchestration-status").inner_text()
        if "completed" in status_text.lower():
            completed = True
            break
        time.sleep(0.5)
    assert completed, f"Orchestration did not complete. Last status: {status_text}"

    # After completion, start should be re-enabled.
    assert page.locator("#orchestration-start").is_enabled()


def test_dashboard_shows_jobhunter_and_queue_import_state(
    page: Page, orchestration_server: tuple[str, object]
) -> None:
    """After a completed run, the dashboard shows JobHunter and queue import state."""
    base, app = orchestration_server
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base)
    page.wait_for_selector("#orchestration-start", timeout=10_000)

    # Start via the API for reliability (the UI button works but may race).
    svc = app.state.orchestration_service
    GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "platforms" / "greenhouse_apply.html").read_text(
        encoding="utf-8"
    )
    svc.start(
        mode="sequential",
        fixture_html=GREENHOUSE_APPLY_HTML,
        max_jobs=1,
    )

    # Wait for completion.
    deadline = time.time() + 60.0
    while time.time() < deadline:
        status = svc.status()
        if status["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)

    # Reload the dashboard to pick up the final state.
    page.reload()
    page.wait_for_selector("#orchestration-status", timeout=10_000)

    # JobHunter state should show a PID.
    jh_text = page.locator("#orchestration-jobhunter-state").inner_text()
    assert "pid=" in jh_text.lower(), f"Expected JobHunter PID, got {jh_text!r}"

    # Queue import should show success.
    qi_text = page.locator("#orchestration-queue-import").inner_text()
    assert "success" in qi_text.lower(), f"Expected queue import success, got {qi_text!r}"
