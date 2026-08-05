"""Integration tests for the WQ-4 background pipeline worker.

The pipeline runs in a dedicated worker subprocess launched by
PipelineWorkerService; the tests observe everything through the durable
``pipeline_runs`` table exposed by GET /api/pipeline/status.

Tests:
- start returns promptly while work continues in the background
- duplicate start is rejected (409)
- pause occurs before the next job and resume continues
- cancel stops future jobs; the worker subprocess exits (browser cleanup)
- run state survives an app restart; a restart cannot start a duplicate run
- errors are persisted and visible on the run row
- one failed job does not erase earlier results
- worker never invokes final submission; no job becomes SUBMITTED or APPLIED

Traffic discipline: fixture-mode runs never touch the network. The single
live-browser test uses a local fixture HTTP server on 127.0.0.1 plus a
deliberately unused port (connection refused) — no external hosts.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.persistence.pipeline_run_repository import (
    get_latest_pipeline_run,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"

GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "greenhouse_apply.html").read_text(encoding="utf-8")


def _make_settings(tmp_path: Path, *, pulse_ms: int = 800) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq4",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
        pipeline_job_pulse_ms=pulse_ms,
    )


def _make_job(
    tmp_path: Path,
    external_id: str,
    url: str,
    platform: Platform = Platform.GREENHOUSE,
) -> ApplicationJob:
    cv = tmp_path / f"{external_id}-cv.pdf"
    cover = tmp_path / f"{external_id}-cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=platform.value, external_job_id=external_id, url=url
        ),
        platform=platform,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id=external_id,
        metadata={
            "candidate_profile": {
                "first_name": "Test",
                "last_name": "User",
                "full_name": "Test User",
                "email": "test@example.com",
                "phone": "+49 123",
                "requires_sponsorship": False,
            },
        },
    )


@contextmanager
def _running_app(tmp_path: Path, jobs: list[ApplicationJob]) -> Any:
    """Create an app over a fresh migrated DB, seed jobs, and yield
    ``(client, app, settings)`` while the app lifespan is active.

    Seeding and the test body share the same TestClient/lifespan so the SQLAlchemy
    engine is created exactly once and disposed exactly once on exit. A seeding
    TestClient followed by a second ``with TestClient(app)`` would let the second
    lifespan reuse the engine without disposing it, leaking a pooled sqlite3
    connection until garbage collection (a ResourceWarning failure on Python 3.14).
    """
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    from universal_auto_applier.persistence.db import build_engine_url

    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)
    with TestClient(app) as client:
        Base.metadata.create_all(app.state.engine)
        with session_scope(app.state.session_factory) as session:
            for job in jobs:
                upsert_application_job(session, job)
        yield client, app, settings


def _wait_for_terminal(client: TestClient, timeout: float = 30.0) -> dict[str, Any]:
    """Poll pipeline status until terminal."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/pipeline/status").json()
        if last["status"] in ("idle", "completed", "cancelled", "failed"):
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Pipeline did not reach terminal state in {timeout}s. Last: {last}")


def _wait_until(client: TestClient, predicate: Any, timeout: float = 20.0) -> dict[str, Any]:
    """Poll pipeline status until the predicate returns True."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/pipeline/status").json()
        if predicate(last):
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Condition not met in {timeout}s. Last: {last}")


def _unused_port() -> int:
    """Return a TCP port on 127.0.0.1 that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FixtureServer:
    """Tiny local HTTP server serving the greenhouse apply fixture HTML."""

    def __init__(self) -> None:
        self._html = GREENHOUSE_APPLY_HTML

        class _Handler(BaseHTTPRequestHandler):
            html = self._html

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(self.html.encode("utf-8"))

            def log_message(self, *args: Any) -> None:  # noqa: ARG002
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class TestStartReturnsPromptly:
    def test_start_returns_immediately(self, tmp_path: Path) -> None:
        """POST /pipeline/start returns 200 with a durable running state."""
        with _running_app(tmp_path, []) as (client, app, settings):
            resp = client.post("/api/pipeline/start", json={"max_jobs": 1})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] in ("running", "completed")
            assert data["run_id"]
            # No jobs -> the run completes quickly.
            final = _wait_for_terminal(client)
            assert final["status"] == "completed"
            assert final["jobs_total"] == 0


class TestDuplicateStartRejected:
    def test_duplicate_start_returns_409(self, tmp_path: Path) -> None:
        """A second start while a run is active returns 409."""
        job = _make_job(tmp_path, "dup-1", url="https://boards.greenhouse.io/example/jobs/dup-1")
        with _running_app(tmp_path, [job]) as (client, app, settings):
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            resp = client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            assert resp.status_code == 409
            assert "already active" in resp.json()["detail"].lower()
            client.post("/api/pipeline/cancel")
            final = _wait_for_terminal(client, timeout=30)
            assert final["status"] == "cancelled"


class TestPauseAndResume:
    def test_pause_between_jobs_and_resume(self, tmp_path: Path) -> None:
        """Pause prevents the next job from starting; resume continues."""
        job1 = _make_job(
            tmp_path, "pause-1", url="https://boards.greenhouse.io/example/jobs/pause-1"
        )
        job2 = _make_job(
            tmp_path, "pause-2", url="https://boards.greenhouse.io/example/jobs/pause-2"
        )
        with _running_app(tmp_path, [job1, job2]) as (client, app, settings):
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            # Wait until job 1 is done (the pulse window is open).
            _wait_until(client, lambda s: s["status"] == "running" and s["jobs_completed"] >= 1)
            resp = client.post("/api/pipeline/pause")
            assert resp.status_code == 200
            assert resp.json()["status"] in ("pausing", "paused")

            paused = _wait_until(client, lambda s: s["status"] == "paused")
            assert paused["jobs_completed"] == 1
            assert paused["jobs_total"] == 2

            resp = client.post("/api/pipeline/resume")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            final = _wait_for_terminal(client, timeout=30)
            assert final["status"] == "completed"
            assert final["jobs_completed"] == 2

            # Both jobs were processed, neither submitted.
            with session_scope(app.state.session_factory) as session:
                for job in (job1, job2):
                    updated = get_application_job(session, job.application_id)
                    assert updated is not None
                    assert str(updated.status) not in (
                        ApplicationStatus.SUBMITTED.value,
                        ApplicationStatus.APPLIED.value,
                    )

    def test_resume_without_pause_is_rejected(self, tmp_path: Path) -> None:
        """Resuming an active (non-paused) run returns 409."""
        with _running_app(tmp_path, []) as (client, app, settings):
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            resp = client.post("/api/pipeline/resume")
            assert resp.status_code == 409
            client.post("/api/pipeline/cancel")
            _wait_for_terminal(client)


class TestCancel:
    def test_cancel_stops_before_next_job_and_exits(self, tmp_path: Path) -> None:
        """Cancel stops the pipeline before the next job; the worker
        subprocess exits (browser/process cleanup)."""
        job1 = _make_job(
            tmp_path, "cancel-1", url="https://boards.greenhouse.io/example/jobs/cancel-1"
        )
        job2 = _make_job(
            tmp_path, "cancel-2", url="https://boards.greenhouse.io/example/jobs/cancel-2"
        )
        with _running_app(tmp_path, [job1, job2]) as (client, app, settings):
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            _wait_until(client, lambda s: s["status"] == "running" and s["jobs_completed"] >= 1)
            resp = client.post("/api/pipeline/cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"

            final = _wait_for_terminal(client, timeout=30)
            assert final["status"] == "cancelled"
            assert final["jobs_completed"] == 1
            assert final["cancel_reason"] == "User cancelled"
            # Terminal state clears the current job.
            assert final["current_job_id"] is None

            # Job 2 was never started.
            with session_scope(app.state.session_factory) as session:
                updated2 = get_application_job(session, job2.application_id)
            assert updated2 is not None
            assert str(updated2.status) not in (
                ApplicationStatus.IN_PROGRESS.value,
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            )

            # The worker subprocess has exited.
            worker = app.state.pipeline_worker
            assert worker._proc is not None  # noqa: SLF001 - test-only introspection
            worker._proc.wait(timeout=10)  # noqa: SLF001
            assert worker._proc.poll() is not None  # noqa: SLF001


class TestStateSurvivesRestart:
    def test_restart_keeps_run_state_and_blocks_duplicate(self, tmp_path: Path) -> None:
        """After an app restart the run state (id, status, counts) is still
        readable and a new start is rejected until the stale run is cancelled."""
        job1 = _make_job(tmp_path, "restart-1", url="https://boards.greenhouse.io/example/jobs/r1")
        job2 = _make_job(tmp_path, "restart-2", url="https://boards.greenhouse.io/example/jobs/r2")
        settings = _make_settings(tmp_path, pulse_ms=1500)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        from universal_auto_applier.persistence.db import build_engine_url

        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        app1 = create_app(settings=settings)
        with TestClient(app1) as client1:
            Base.metadata.create_all(app1.state.engine)
            with session_scope(app1.state.session_factory) as session:
                for job in (job1, job2):
                    upsert_application_job(session, job)
            resp = client1.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]
            _wait_until(
                client1,
                lambda s: s["status"] == "running" and s["jobs_completed"] >= 1,
            )
            client1.post("/api/pipeline/pause")
            paused = _wait_until(client1, lambda s: s["status"] == "paused")
            assert paused["jobs_completed"] == 1

        # The paused run survives: read it with a fresh app over the same DB.
        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            state = client2.get("/api/pipeline/status").json()
            assert state["run_id"] == run_id
            assert state["status"] == "paused"
            assert state["jobs_completed"] == 1
            assert state["jobs_total"] == 2

            # A duplicate start is refused while the stale run is active.
            resp = client2.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            assert resp.status_code == 409

            # Cancel recovers the stale run directly (no live worker).
            resp = client2.post("/api/pipeline/cancel")
            assert resp.status_code == 200
            final = _wait_for_terminal(client2, timeout=30)
            assert final["status"] == "cancelled"
            assert final["run_id"] == run_id

            # A fresh start works afterwards. Only job 2 is still eligible —
            # job 1's durable outcome from the paused run is preserved and
            # the new run must not re-process it.
            resp = client2.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            assert resp.status_code == 200
            assert resp.json()["run_id"] != run_id
            final = _wait_for_terminal(client2, timeout=30)
            assert final["status"] == "completed"
            assert final["jobs_total"] == 1
            assert final["jobs_completed"] == 1

            with session_scope(app2.state.session_factory) as session:
                j1 = get_application_job(session, job1.application_id)
                j2 = get_application_job(session, job2.application_id)
            assert j1 is not None
            assert j2 is not None
            assert str(j1.status) not in (
                ApplicationStatus.READY_TO_APPLY.value,
                ApplicationStatus.QUEUED.value,
                ApplicationStatus.IN_PROGRESS.value,
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            )
            assert str(j2.status) in (
                ApplicationStatus.REVIEW_READY.value,
                ApplicationStatus.NEEDS_USER_INPUT.value,
            )


class TestNoSubmission:
    def test_no_job_becomes_submitted(self, tmp_path: Path) -> None:
        """No job transitions to SUBMITTED or APPLIED from the worker."""
        job = _make_job(tmp_path, "no-submit-1", url="https://boards.greenhouse.io/example/jobs/n1")
        with _running_app(tmp_path, [job]) as (client, app, settings):
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
            )
            final = _wait_for_terminal(client, timeout=30)
            assert final["status"] == "completed"
            assert final["jobs_completed"] == 1

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
            assert updated is not None
            assert str(updated.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job became {updated.status} — worker must not submit!"
            assert str(updated.status) in (
                ApplicationStatus.REVIEW_READY.value,
                ApplicationStatus.NEEDS_USER_INPUT.value,
            )


class TestErrorsVisible:
    def test_failed_job_records_durable_error(self, tmp_path: Path) -> None:
        """A job that fails (connection refused, no external host) records a
        durable error on the run row without crashing the run."""
        port = _unused_port()
        job = _make_job(tmp_path, "fail-1", url=f"http://127.0.0.1:{port}/apply")
        with _running_app(tmp_path, [job]) as (client, app, settings):
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            final = _wait_for_terminal(client, timeout=60)
            assert final["status"] == "completed"
            assert final["jobs_failed"] == 1

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
                run_row = get_latest_pipeline_run(session)
            assert updated is not None
            assert str(updated.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            )
            assert run_row is not None
            assert run_row.errors_json, "expected a durable error entry"
            assert run_row.errors_json[0]["application_id"] == job.application_id
            assert run_row.errors_json[0]["error"]


class TestOneFailedJobDoesNotEraseResults:
    def test_previous_results_preserved(self, tmp_path: Path) -> None:
        """Live run: job 1 succeeds via a local fixture server and job 2
        fails; job 1's outcome and the run counters are preserved."""
        server = _FixtureServer()
        try:
            job1 = _make_job(
                tmp_path,
                "ok-1",
                url=f"http://127.0.0.1:{server.port}/apply",
            )
            port2 = _unused_port()
            job2 = _make_job(tmp_path, "fail-2", url=f"http://127.0.0.1:{port2}/apply")
            with _running_app(tmp_path, [job1, job2]) as (client, app, settings):
                client.post("/api/pipeline/start", json={"max_jobs": 2})
                final = _wait_for_terminal(client, timeout=90)
                assert final["status"] == "completed"
                assert final["jobs_completed"] == 1
                assert final["jobs_failed"] == 1

                with session_scope(app.state.session_factory) as session:
                    updated1 = get_application_job(session, job1.application_id)
                    updated2 = get_application_job(session, job2.application_id)
                assert updated1 is not None
                assert updated2 is not None
                # Job 1 was processed and kept its outcome.
                assert str(updated1.status) not in (
                    ApplicationStatus.SUBMITTED.value,
                    ApplicationStatus.APPLIED.value,
                )
                assert str(updated1.status) not in (
                    ApplicationStatus.READY_TO_APPLY.value,
                    ApplicationStatus.QUEUED.value,
                    ApplicationStatus.IN_PROGRESS.value,
                )
                # Job 2 failed or needs input, never submitted.
                assert str(updated2.status) in (
                    ApplicationStatus.FAILED.value,
                    ApplicationStatus.NEEDS_USER_INPUT.value,
                )
        finally:
            server.stop()
