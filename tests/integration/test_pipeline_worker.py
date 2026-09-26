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

import json
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

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
from universal_auto_applier.persistence.models import (
    ApplicationAttemptRow,
    Base,
    PhaseResultRow,
)
from universal_auto_applier.persistence.pipeline_run_repository import (
    get_latest_pipeline_run,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "platforms"

GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "greenhouse_apply.html").read_text(encoding="utf-8")


def _make_settings(
    tmp_path: Path,
    *,
    pulse_ms: int = 800,
    queue_path: Path | None = None,
) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq4",
        queue_path=queue_path,
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
def _running_app(
    tmp_path: Path,
    jobs: list[ApplicationJob],
    *,
    queue_path: Path | None = None,
) -> Any:
    """Create an app over a fresh migrated DB, seed jobs, and yield
    ``(client, app, settings)`` while the app lifespan is active.

    Seeding and the test body share the same TestClient/lifespan so the SQLAlchemy
    engine is created exactly once and disposed exactly once on exit. A seeding
    TestClient followed by a second ``with TestClient(app)`` would let the second
    lifespan reuse the engine without disposing it, leaking a pooled sqlite3
    connection until garbage collection (a ResourceWarning failure on Python 3.14).
    """
    settings = _make_settings(tmp_path, queue_path=queue_path)
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


def _synthetic_worker_queue(tmp_path: Path, url: str, external_id: str) -> tuple[Path, str]:
    """Write one synthetic, ready-to-apply queue row and its fake documents."""
    cv_path = tmp_path / f"{external_id}-synthetic-cv.pdf"
    cover_path = tmp_path / f"{external_id}-synthetic-cover.pdf"
    cv_path.write_bytes(b"%PDF-1.4\nSynthetic CV fixture\n")
    cover_path.write_bytes(b"%PDF-1.4\nSynthetic cover letter fixture\n")
    application_id = compute_application_id(
        platform=Platform.GENERIC.value,
        external_job_id=external_id,
        url=url,
    )
    row = {
        "application_id": application_id,
        "platform": Platform.GENERIC.value,
        "source": "synthetic-fixture",
        "company": "Synthetic Fixture Co",
        "title": "Synthetic Engineer",
        "url": url,
        "location": "Erlangen, Germany",
        "job_description": "Synthetic local worker acceptance fixture.",
        "verdict": "apply",
        "cv_pdf": str(cv_path),
        "cover_letter_pdf": str(cover_path),
        "status": ApplicationStatus.READY_TO_APPLY.value,
        "external_job_id": external_id,
        "metadata": {
            "candidate_profile": {
                "full_name": "Synthetic Candidate",
                "first_name": "Synthetic",
                "last_name": "Candidate",
                "email": "synthetic.candidate@example.test",
                "city": "Erlangen",
                "country": "Germany",
                "requires_sponsorship": False,
                "work_authorization": "Yes",
            }
        },
    }
    queue_path = tmp_path / f"{external_id}-application_queue.jsonl"
    queue_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return queue_path, application_id


def _worker_preparation_fixture(
    *,
    extra_required_field: bool = False,
    include_upload: bool = False,
    denied_post: bool = False,
) -> str:
    fixture_path = (
        Path(__file__).parent.parent
        / "fixtures"
        / "platforms"
        / ("worker_preparation_same_url_nested.html")
    )
    extra = (
        '<label for="preferred_language">Preferred programming language</label>'
        '<input id="preferred_language" name="preferred_language" required>'
        if extra_required_field
        else ""
    )
    upload = (
        '<label for="resume">Resume</label><input id="resume" name="resume" type="file" required>'
        if include_upload
        else ""
    )
    html = fixture_path.read_text(encoding="utf-8")
    html = html.replace("__EXTRA_REQUIRED_FIELD__", extra).replace("__UPLOAD_CONTROL__", upload)
    if denied_post:
        html = html.replace(
            "</body>",
            "<script>fetch('/autosave', {method: 'POST', body: 'synthetic fixture'}).catch(() => {});</script></body>",
        )
    return html


class _FixtureServer:
    """Tiny local HTTP server recording page and application requests."""

    def __init__(self, html: str | None = None) -> None:
        self._html = html or GREENHOUSE_APPLY_HTML
        self.get_count = 0
        self.post_count = 0
        self.request_paths: list[tuple[str, str]] = []
        self._request_lock = threading.Lock()
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            html = self._html

            def do_GET(self) -> None:  # noqa: N802
                with owner._request_lock:
                    owner.get_count += 1
                    owner.request_paths.append(("GET", self.path))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(self.html.encode("utf-8"))

            def do_POST(self) -> None:  # noqa: N802
                with owner._request_lock:
                    owner.post_count += 1
                    owner.request_paths.append(("POST", self.path))
                self.send_response(204)
                self.end_headers()

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

    def test_paused_worker_updates_durable_heartbeat(self, tmp_path: Path) -> None:
        """Durable heartbeat proof (WQ-5 acceptance).

        The real WQ-4 subprocess worker (fixture mode, local data only) owns
        the run while paused: it continuously refreshes ``heartbeat_at`` from
        inside its paused polling loop, keeps its durable ``worker_pid``, and
        never becomes ``recovered``. After cancel the worker subprocess exits
        cleanly (no ResourceWarning — enforced by the global
        ``filterwarnings = ["error::ResourceWarning"]``).

        Nothing in this test fakes the heartbeat by writing the DB row
        directly: every ``heartbeat_at`` advance is performed by the worker
        subprocess via ``_touch_heartbeat``.
        """
        job1 = _make_job(
            tmp_path, "hb-pause-1", url="https://boards.greenhouse.io/example/jobs/hb-pause-1"
        )
        job2 = _make_job(
            tmp_path, "hb-pause-2", url="https://boards.greenhouse.io/example/jobs/hb-pause-2"
        )
        with _running_app(tmp_path, [job1, job2]) as (client, app, settings):
            client.post(
                "/api/pipeline/start",
                json={"fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 2},
            )
            _wait_until(client, lambda s: s["status"] == "running" and s["jobs_completed"] >= 1)
            resp = client.post("/api/pipeline/pause")
            assert resp.status_code == 200

            paused_status = _wait_until(client, lambda s: s["status"] == "paused")
            assert paused_status["status"] == "paused"

            with session_scope(app.state.session_factory) as session:
                run_row = get_latest_pipeline_run(session)
                assert run_row is not None
                initial_heartbeat = run_row.heartbeat_at
                worker_pid = run_row.worker_pid
                assert worker_pid is not None
                assert run_row.status == "paused"
                # A healthy paused run must not look recovered: recovery only
                # touches proven-stale rows (dead pid AND expired heartbeat).
                assert run_row.status != "recovered"

            # Wait long enough for the paused worker polling loop
            # (_PAUSE_POLL_SECONDS = 0.2) to run several more iterations,
            # each of which calls _touch_heartbeat.
            time.sleep(0.6)

            with session_scope(app.state.session_factory) as session:
                updated_row = get_latest_pipeline_run(session)
                assert updated_row is not None
                # Status remains paused — the run is NOT recovered while the
                # worker is alive and heartbeating.
                assert updated_row.status == "paused"
                assert updated_row.status != "recovered"
                # Durable worker PID remains present and unchanged.
                assert updated_row.worker_pid is not None
                assert updated_row.worker_pid == worker_pid
                # Heartbeat advanced from the worker subprocess, not from us.
                assert updated_row.heartbeat_at is not None
                assert initial_heartbeat is not None
                assert updated_row.heartbeat_at > initial_heartbeat

            # Cancel cleanly and verify the worker subprocess exits. The global
            # ``filterwarnings = ["error::ResourceWarning"]`` config makes any
            # ResourceWarning a test failure; explicitly waiting for the
            # subprocess to exit ensures its resources (browser, pipes, engine)
            # are released before the test ends.
            cancel_resp = client.post("/api/pipeline/cancel")
            assert cancel_resp.status_code == 200
            final = _wait_for_terminal(client, timeout=30)
            assert final["status"] == "cancelled"

            worker = app.state.pipeline_worker
            assert worker._proc is not None  # noqa: SLF001 - test-only introspection
            worker._proc.wait(timeout=10)  # noqa: SLF001
            assert worker._proc.poll() is not None  # noqa: SLF001


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


class TestProductionWorkerPreparation:
    def _import_and_start(
        self,
        client: TestClient,
        queue_path: Path,
    ) -> dict[str, Any]:
        imported = client.post("/api/queue/import")
        assert imported.status_code == 200, imported.text
        assert imported.json()["run"]["state"] == "success"
        assert imported.json()["run"]["imported"] == 1
        started = client.post("/api/pipeline/start", json={"max_jobs": 1})
        assert started.status_code == 200, started.text
        assert started.json()["mode"] == "sequential_dry_run"
        return _wait_for_terminal(client, timeout=90)

    def test_real_worker_persists_three_step_nested_review_snapshot(self, tmp_path: Path) -> None:
        """The API import and subprocess worker persist complete same-URL evidence."""
        server = _FixtureServer(_worker_preparation_fixture())
        try:
            queue_path, application_id = _synthetic_worker_queue(
                tmp_path,
                f"http://127.0.0.1:{server.port}/apply",
                "worker-preparation-ready",
            )
            with _running_app(tmp_path, [], queue_path=queue_path) as (client, app, _settings):
                final = self._import_and_start(client, queue_path)
                review = client.get(f"/api/submit/{application_id}/status").json()["snapshot"]
                dashboard = client.get("/api/status").json()
                interventions = client.get(
                    "/api/interventions",
                    params={"application_id": application_id, "pending_only": "true"},
                ).json()

                with session_scope(app.state.session_factory) as session:
                    persisted_job = get_application_job(session, application_id)
                    from universal_auto_applier.submission.store import get_active_approval

                    approval = get_active_approval(session, application_id)
                    attempts = session.scalars(
                        select(ApplicationAttemptRow).where(
                            ApplicationAttemptRow.application_id == application_id
                        )
                    ).all()
                    phase_results = (
                        session.scalars(
                            select(PhaseResultRow).where(
                                PhaseResultRow.attempt_id == attempts[0].attempt_id
                            )
                        ).all()
                        if attempts
                        else []
                    )

                assert final["status"] == "completed"
                assert final["jobs_completed"] == 1
                assert final["jobs_failed"] == 0
                field_states = [
                    (field["label"], field["status"], field["required"])
                    for field in review["fields"]
                ]
                upload_states = [
                    (
                        doc["document_kind"],
                        doc["status"],
                        doc["evidence_source"],
                        doc["upload_contract"],
                    )
                    for doc in review["documents"]
                ]
                assert "verified review boundary" in final["last_action"], (
                    f"field states={field_states} upload states={upload_states}"
                )
                assert persisted_job is not None
                assert str(persisted_job.status) == ApplicationStatus.REVIEW_READY.value
                assert approval is not None
                assert len(attempts) == 1
                assert attempts[0].status == ApplicationStatus.REVIEW_READY.value
                assert attempts[0].finished_at is not None
                assert attempts[0].mode == "review"
                assert len(phase_results) == 1
                assert phase_results[0].phase == "prepare"
                assert phase_results[0].status == "review_ready"
                assert phase_results[0].metadata_json == {"preparation_outcome": "review_ready"}
                persisted_snapshot = approval.snapshot_json
                assert persisted_snapshot["completed_form_step_count"] == 3
                assert persisted_snapshot["final_boundary_confirmed"] is True
                assert len(persisted_snapshot["form_progress_fingerprint"]) >= 32
                assert {field["label"] for field in persisted_snapshot["fields"]} >= {
                    "First name",
                    "Last name",
                    "Do you require sponsorship?",
                    "Are you authorized to work?",
                    "City",
                    "Email address",
                }
                assert review["application_status"] == ApplicationStatus.REVIEW_READY.value
                assert review["application_url"] == f"http://127.0.0.1:{server.port}/apply"
                assert review["completed_form_step_count"] == 3
                assert review["final_boundary_confirmed"] is True
                assert review["can_approve"] is True
                assert review["pending_intervention_count"] == 0
                assert review["documents"] == []
                assert dashboard["jobs_by_status"][ApplicationStatus.REVIEW_READY.value] == 1
                assert interventions["total"] == 0
                assert server.get_count >= 1
                assert {path for method, path in server.request_paths if method == "GET"} == {
                    "/apply"
                }
                assert server.post_count == 0
        finally:
            server.stop()

    def test_unresolved_required_field_is_intervened_and_worker_will_not_retry(
        self,
        tmp_path: Path,
    ) -> None:
        server = _FixtureServer(_worker_preparation_fixture(extra_required_field=True))
        try:
            queue_path, application_id = _synthetic_worker_queue(
                tmp_path,
                f"http://127.0.0.1:{server.port}/apply",
                "worker-preparation-unresolved",
            )
            with _running_app(tmp_path, [], queue_path=queue_path) as (client, app, _settings):
                final = self._import_and_start(client, queue_path)
                review = client.get(f"/api/submit/{application_id}/status").json()["snapshot"]
                listed = client.get(
                    "/api/interventions",
                    params={"application_id": application_id, "pending_only": "true"},
                ).json()["interventions"]

                with session_scope(app.state.session_factory) as session:
                    persisted_job = get_application_job(session, application_id)

                assert final["status"] == "completed"
                assert final["jobs_completed"] == 1
                assert "1 required field(s) remain unresolved" in final["last_error"]
                assert "Next action:" in final["last_action"]
                assert persisted_job is not None
                assert str(persisted_job.status) == ApplicationStatus.NEEDS_USER_INPUT.value
                assert review["final_boundary_confirmed"] is True
                assert review["can_approve"] is False
                assert review["unresolved_required_field_count"] == 1
                field_interventions = [item for item in listed if item["kind"] == "field_answer"]
                assert len(field_interventions) == 1
                assert field_interventions[0]["llm_metadata"]["field_label"] == (
                    "Preferred programming language"
                )
                assert field_interventions[0]["field_selector"]
                assert server.post_count == 0

                before_retry_count = server.get_count
                retried = client.post("/api/pipeline/start", json={"max_jobs": 1})
                assert retried.status_code == 200, retried.text
                second_run = _wait_for_terminal(client, timeout=30)
                assert second_run["status"] == "completed"
                assert second_run["jobs_total"] == 0
                assert server.get_count == before_retry_count
                assert server.post_count == 0
        finally:
            server.stop()

    def test_native_upload_without_qualified_flow_stays_blocked(self, tmp_path: Path) -> None:
        server = _FixtureServer(_worker_preparation_fixture(include_upload=True))
        try:
            queue_path, application_id = _synthetic_worker_queue(
                tmp_path,
                f"http://127.0.0.1:{server.port}/apply",
                "worker-preparation-upload-unqualified",
            )
            with _running_app(tmp_path, [], queue_path=queue_path) as (client, app, _settings):
                final = self._import_and_start(client, queue_path)
                review = client.get(f"/api/submit/{application_id}/status").json()["snapshot"]
                listed = client.get(
                    "/api/interventions",
                    params={"application_id": application_id, "pending_only": "true"},
                ).json()["interventions"]

                with session_scope(app.state.session_factory) as session:
                    persisted_job = get_application_job(session, application_id)

                assert final["status"] == "completed"
                assert final["jobs_completed"] == 1
                assert "document upload(s) lack complete evidence" in final["last_error"]
                assert "Next action:" in final["last_action"]
                assert persisted_job is not None
                assert str(persisted_job.status) == ApplicationStatus.NEEDS_USER_INPUT.value
                assert review["can_approve"] is False
                assert review["unresolved_required_field_count"] == 1
                assert review["unresolved_upload_count"] == 1
                assert len(review["documents"]) == 1
                document = review["documents"][0]
                assert document["document_kind"] == "cv"
                assert document["status"] == "selection_verified"
                assert document["evidence_source"] == "native_selection"
                assert document["upload_contract"] is None
                field_interventions = [item for item in listed if item["kind"] == "field_answer"]
                assert len(field_interventions) == 1
                assert field_interventions[0]["llm_metadata"]["field_label"] == "Resume"
                assert server.post_count == 0

                before_retry_count = server.get_count
                retried = client.post("/api/pipeline/start", json={"max_jobs": 1})
                assert retried.status_code == 200, retried.text
                second_run = _wait_for_terminal(client, timeout=30)
                assert second_run["status"] == "completed"
                assert second_run["jobs_total"] == 0
                assert server.get_count == before_retry_count
                assert server.post_count == 0
        finally:
            server.stop()

    def test_denied_http_mutation_stays_blocked_and_is_not_retried(self, tmp_path: Path) -> None:
        server = _FixtureServer(_worker_preparation_fixture(denied_post=True))
        try:
            queue_path, application_id = _synthetic_worker_queue(
                tmp_path,
                f"http://127.0.0.1:{server.port}/apply",
                "worker-preparation-http-blocked",
            )
            with _running_app(tmp_path, [], queue_path=queue_path) as (client, app, _settings):
                final = self._import_and_start(client, queue_path)
                review = client.get(f"/api/submit/{application_id}/status").json()["snapshot"]
                listed = client.get(
                    "/api/interventions",
                    params={"application_id": application_id, "pending_only": "true"},
                ).json()["interventions"]

                with session_scope(app.state.session_factory) as session:
                    persisted_job = get_application_job(session, application_id)

                assert final["status"] == "completed"
                assert final["jobs_completed"] == 1
                assert "preparation_http_mutation_blocked" in final["last_error"]
                assert "before retrying" in final["last_action"]
                assert persisted_job is not None
                assert str(persisted_job.status) == ApplicationStatus.NEEDS_USER_INPUT.value
                assert review["snapshot_hash"] == ""
                assert review["can_approve"] is False
                http_blockers = [
                    item for item in listed if item["kind"] == "preparation_http_mutation_blocked"
                ]
                assert len(http_blockers) == 1
                assert http_blockers[0]["llm_metadata"]["blocked_request_count"] >= 1
                assert http_blockers[0]["llm_metadata"]["blocked_requests"][0]["method"] == "POST"
                assert server.get_count >= 1
                assert server.post_count == 0

                before_retry_count = server.get_count
                retried = client.post("/api/pipeline/start", json={"max_jobs": 1})
                assert retried.status_code == 200, retried.text
                second_run = _wait_for_terminal(client, timeout=30)
                assert second_run["status"] == "completed"
                assert second_run["jobs_total"] == 0
                # The API observer also refuses the retry before browser creation.
                observation_retry = client.post(f"/api/submit/{application_id}/observe")
                assert observation_retry.status_code == 409
                assert "preparation_http_mutation_blocked" in observation_retry.json()["detail"]
                assert server.get_count == before_retry_count
                assert server.post_count == 0
        finally:
            server.stop()
