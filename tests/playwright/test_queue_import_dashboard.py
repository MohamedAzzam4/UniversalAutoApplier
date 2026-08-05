"""Playwright test: WQ-3 Queue Import card in the dashboard.

User-POV verification for WQ-3:

- The Queue Import card renders configuration and startup-import state.
- With a configured queue the "Import Queue" button is enabled; clicking it
  imports the queue through the API and shows the durable run result.
- With no queue configured the button is disabled and the card says so.
- Importing only touches local history (no submit/apply/pipeline started).

All tests run against the real local dashboard API and SQLite store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id

pytestmark = pytest.mark.playwright


def _make_valid_job_line(
    *,
    url: str = "https://example.com/jobs/123",
    external_job_id: str = "dashboard-job-1",
) -> str:
    application_id = compute_application_id(
        platform="greenhouse", external_job_id=external_job_id, url=url
    )
    return json.dumps(
        {
            "application_id": application_id,
            "platform": "greenhouse",
            "source": "linkedin",
            "company": "Dashboard GmbH",
            "title": "Working Student",
            "url": url,
            "location": "Munich, Germany",
            "job_description": "Full JD",
            "score": 4.1,
            "verdict": "apply",
            "cv_pdf": None,
            "cover_letter_pdf": None,
            "status": "evaluated",
            "external_job_id": external_job_id,
        }
    )


def _start_dashboard(
    tmp_path: Path,
    *,
    queue_path: Path | None,
    port: int,
) -> tuple[str, object, object]:
    """Start the dashboard on ``port`` with an optional configured queue."""
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_data",
        queue_path=queue_path,
        browser_headless=True,
        submit_mode="review",
    )
    tmp_path.joinpath("uaa_data").mkdir(parents=True, exist_ok=True)
    from universal_auto_applier.persistence.db import build_engine_url
    from universal_auto_applier.persistence.migrations import apply_migrations

    apply_migrations(build_engine_url(tmp_path / "uaa_data" / "uaa.sqlite"))
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
    import socket
    import threading
    import time
    from contextlib import closing

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
    return base, app, server


def _free_port() -> int:
    import socket
    from contextlib import closing

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestQueueImportCard:
    def test_card_renders_when_not_configured(self, page: Page, tmp_path: Path) -> None:
        base, app, server = _start_dashboard(
            tmp_path / "uaa_q_not_configured", queue_path=None, port=_free_port()
        )
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.wait_for_function(
                "document.getElementById('queue-import-status')"
                ".textContent.includes('not_configured')",
                timeout=10_000,
            )
            text = page.locator("#queue-import-status").inner_text().lower()
            assert "configuration" in text
            assert "not_configured" in text
            assert "queue path" in text
            run_btn = page.locator("#queue-import-run")
            assert run_btn.is_disabled()
        finally:
            server.should_exit = True

    def test_card_renders_when_configured(self, page: Page, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line() + "\n", encoding="utf-8")
        base, app, server = _start_dashboard(
            tmp_path / "uaa_q_configured", queue_path=queue_path, port=_free_port()
        )
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.wait_for_function(
                "document.getElementById('queue-import-status').textContent.includes('configured')",
                timeout=10_000,
            )
            text = page.locator("#queue-import-status").inner_text()
            assert "configured" in text
            assert str(queue_path) in text
            assert "No imports recorded yet." in text
            assert page.locator("#queue-import-run").is_enabled()
        finally:
            server.should_exit = True

    def test_clicking_import_shows_run_result(self, page: Page, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line() + "\n", encoding="utf-8")
        base, app, server = _start_dashboard(
            tmp_path / "uaa_q_import", queue_path=queue_path, port=_free_port()
        )
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.wait_for_function(
                "document.getElementById('queue-import-run') && "
                "!document.getElementById('queue-import-run').disabled",
                timeout=10_000,
            )
            page.click("#queue-import-run")

            # The status grid refresh shows the durable run details.
            page.wait_for_function(
                "document.getElementById('queue-import-status')"
                ".textContent.includes('Last Import')"
                " && document.getElementById('queue-import-status')"
                ".textContent.includes('success')",
                timeout=10_000,
            )
            status_text = page.locator("#queue-import-status").inner_text().lower()
            assert "last import" in status_text
            assert "success" in status_text
            # Label and value render as separate spans: "imported\n1".
            assert "imported" in status_text
            assert "jobs in history" in status_text
        finally:
            server.should_exit = True

    def test_import_does_not_touch_submit_or_pipeline(self, page: Page, tmp_path: Path) -> None:
        from universal_auto_applier.core.statuses import ApplicationStatus
        from universal_auto_applier.persistence.db import session_scope
        from universal_auto_applier.persistence.job_repository import list_application_jobs
        from universal_auto_applier.persistence.models import (
            SubmissionResultRow,
            SystemRunRow,
        )

        queue_path = tmp_path / "queue.jsonl"
        queue_path.write_text(_make_valid_job_line() + "\n", encoding="utf-8")
        base, app, server = _start_dashboard(
            tmp_path / "uaa_q_safety", queue_path=queue_path, port=_free_port()
        )
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.wait_for_function(
                "document.getElementById('queue-import-run') && "
                "!document.getElementById('queue-import-run').disabled",
                timeout=10_000,
            )
            page.click("#queue-import-run")
            page.wait_for_function(
                "document.getElementById('queue-import-status')"
                ".textContent.includes('Last Import')"
                " && document.getElementById('queue-import-status')"
                ".textContent.includes('success')",
                timeout=10_000,
            )

            # The import created exactly one job at status evaluated; it did
            # NOT create submission rows or system runs, and it did not move
            # the job toward submission.
            with session_scope(app.state.session_factory) as session:
                jobs = list_application_jobs(session)
                assert len(jobs) == 1
                assert jobs[0].status == ApplicationStatus.EVALUATED.value
                assert session.query(SubmissionResultRow).count() == 0
                assert session.query(SystemRunRow).count() == 0
        finally:
            server.should_exit = True
