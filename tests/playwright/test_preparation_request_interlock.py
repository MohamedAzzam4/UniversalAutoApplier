"""Synthetic browser tests for submit and HTTP mutation interlocks.

The local fixture server counts received POSTs. Its application page runs a
native onchange form submission and a fetch POST, while safe GET navigation
and normal field filling must continue to work.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform

pytestmark = pytest.mark.playwright

APPLICATION_HTML = """<!doctype html>
<html><head><title>Application</title></head><body>
<form id="application" method="post" action="/submit">
  <label for="first_name">First Name</label>
  <input id="first_name" name="first_name"
         onchange="document.getElementById('application').submit()">
  <label for="email">Email</label>
  <input id="email" name="email" type="email">
  <button type="submit">Submit application</button>
</form>
<script>
  document.getElementById('email').addEventListener('change', () => {
    fetch('/ajax-submit?ticket=PRIVATE_QUERY', {
      method: 'POST', body: 'secret=PRIVATE_BODY'
    });
    navigator.sendBeacon('/beacon?ticket=PRIVATE_QUERY', 'secret=PRIVATE_BODY');
  });
  fetch('/readonly?token=PRIVATE_QUERY', {method: 'GET'});
</script>
</body></html>"""


class _RecordingServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.counts_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _RecordingHandler)

    def record(self, key: str) -> None:
        with self.counts_lock:
            self.counts[key] += 1

    def count(self, key: str) -> int:
        with self.counts_lock:
            return self.counts[key]


class _RecordingHandler(BaseHTTPRequestHandler):
    server: _RecordingServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/job":
            self.server.record("job_get")
            self._send_html('<a href="/apply" id="apply" target="_blank">Apply now</a>')
        elif path == "/apply":
            self.server.record("apply_get")
            self._send_html(APPLICATION_HTML)
        elif path == "/readonly":
            self.server.record("readonly_get")
            self._send_html("read-only response")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        # Read and discard the request body; never persist test payloads.
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        self.server.record("post")
        self._send_html("received")

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture
def request_fixture_server() -> Iterator[tuple[_RecordingServer, str]]:
    server = _RecordingServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield server, f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_job(url: str, tmp_path: Path) -> ApplicationJob:
    external_id = "preparation-request-interlock"
    cv_path = tmp_path / "synthetic-cv.pdf"
    cover_path = tmp_path / "synthetic-cover.pdf"
    cv_path.write_bytes(b"%PDF-1.4 synthetic fixture")
    cover_path.write_bytes(b"%PDF-1.4 synthetic fixture")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=Platform.GENERIC.value,
            external_job_id=external_id,
            url=url,
        ),
        platform=Platform.GENERIC,
        source="fixture",
        company="Fixture Company",
        title="Automation Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.READY_TO_APPLY,
        cv_pdf=str(cv_path),
        cover_letter_pdf=str(cover_path),
        external_job_id=external_id,
        metadata={
            "candidate_profile": {
                "first_name": "Avery",
                "last_name": "Example",
                "full_name": "Avery Example",
                "email": "avery@example.invalid",
            }
        },
    )


def test_preparation_blocks_onchange_and_fetch_posts_but_keeps_safe_navigation(
    browser: Browser,
    request_fixture_server: tuple[_RecordingServer, str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server, origin = request_fixture_server
    runner = LiveBrowserRunner(
        LiveBrowserConfig(
            artifacts_root=tmp_path / "live-runs",
            headless=True,
            capture_trace=False,
            timeout_ms=10_000,
        )
    )

    context = browser.new_context(accept_downloads=False, service_workers="block")
    try:
        report = runner.run_in_context(
            context,
            _make_job(f"{origin}/job", tmp_path),
            artifact_dir=tmp_path / "live-runs",
        )
    finally:
        context.close()

    assert report.request_interlock_installed is True
    assert report.request_interlock_coverage == "playwright_context_http_routes"
    assert any("WebSocket" in item for item in report.request_interlock_limitations)
    assert any("Dormant service workers" in item for item in report.request_interlock_limitations)
    assert report.submit_interlock is not None
    assert report.submit_interlock.installed is True
    assert report.submit_interlock.form_submit_calls >= 1
    assert report.status == "needs_user_input"
    assert report.stopped_reason == "mutating_request_blocked"
    assert report.blocked_http_request_count >= 1
    assert any(
        evidence.method == "POST" and evidence.reason == "mutating_method"
        for evidence in report.blocked_http_requests
    )
    assert any(
        evidence.resource_type == "ping" and evidence.reason == "mutating_method"
        for evidence in report.blocked_http_requests
    )

    # Normal GET job/apply navigation and mapped text fields remain functional.
    assert server.count("job_get") == 1
    assert server.count("apply_get") == 1
    assert server.count("readonly_get") >= 1
    assert server.count("post") == 0
    assert any(item.status == "filled" and item.filled_value == "Avery" for item in report.fields)
    assert any(
        item.status == "filled" and item.filled_value == "avery@example.invalid"
        for item in report.fields
    )

    # Evidence contains origin/method only. It never persists URL query values
    # or the POST body, both of which are private-looking fixture sentinels.
    serialized = report.model_dump_json()
    assert "PRIVATE_QUERY" not in serialized
    assert "PRIVATE_BODY" not in serialized
    assert "PRIVATE_QUERY" not in caplog.text
    assert "PRIVATE_BODY" not in caplog.text
    assert all(item.destination_origin == origin for item in report.blocked_http_requests)


@pytest.mark.parametrize(
    "viewport_size",
    [{"width": 1440, "height": 900}, {"width": 390, "height": 844}],
    ids=["desktop-1440x900", "mobile-390x844"],
)
def test_request_guard_behavior_at_required_viewports(
    browser: Browser,
    request_fixture_server: tuple[_RecordingServer, str],
    tmp_path: Path,
    viewport_size: dict[str, int],
) -> None:
    """Check the local guarded flow at desktop and mobile viewport sizes."""
    server, origin = request_fixture_server
    runner = LiveBrowserRunner(
        LiveBrowserConfig(
            artifacts_root=tmp_path / "live-runs",
            headless=True,
            capture_trace=False,
            timeout_ms=10_000,
        )
    )

    context = browser.new_context(
        viewport=viewport_size,
        accept_downloads=False,
        service_workers="block",
    )
    try:
        report = runner.run_in_context(
            context,
            _make_job(f"{origin}/job", tmp_path),
            artifact_dir=tmp_path / f"viewport-{viewport_size['width']}x{viewport_size['height']}",
        )
    finally:
        context.close()

    assert report.request_interlock_installed is True
    assert report.status == "needs_user_input"
    assert report.stopped_reason == "mutating_request_blocked"
    assert server.count("job_get") == 1
    assert server.count("apply_get") == 1
    assert server.count("post") == 0
    assert any(item.status == "filled" and item.filled_value == "Avery" for item in report.fields)


def test_default_preparation_config_keeps_submit_block_enabled(tmp_path: Path) -> None:
    config = LiveBrowserConfig(artifacts_root=tmp_path)
    assert config.hard_submit_block is True
