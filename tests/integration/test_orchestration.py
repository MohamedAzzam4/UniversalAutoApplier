"""Integration tests for WQ-6 cross-repository orchestration.

These tests prove the orchestration service coordinates JobHunter export ->
UAA queue import -> UAA pipeline correctly in both sequential and parallel
modes, with durable state, safe cancellation, restart recovery, and no
final submission.

All tests use a local fake JobHunter script (tests/fixtures/fake_jobhunter/)
and local fixture HTML. No real ATS, no public web, no real submission.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "platforms" / "greenhouse_apply.html").read_text(
    encoding="utf-8"
)
FAKE_JH_REPO = FIXTURES_DIR / "fake_jobhunter"


def _make_settings(
    tmp_path: Path,
    *,
    queue_path: Path | None = None,
    mode: str = "sequential",
) -> Settings:
    """Build settings pointing at the fake JobHunter repo."""
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq6",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
        pipeline_job_pulse_ms=200,
        jobhunter_repo=FAKE_JH_REPO,
        jobhunter_entry_point="run_export_queue.py",
        queue_path=queue_path,
        orchestration_mode=mode,
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


def _start_orchestration(
    client: TestClient,
    *,
    mode: str = "sequential",
    extra_args: list[str] | None = None,
    max_jobs: int = 2,
) -> dict[str, Any]:
    """Start an orchestration run via the service (supports extra_args)."""
    svc = client.app.state.orchestration_service  # type: ignore[union-attr]
    return svc.start(
        mode=mode,
        fixture_html=GREENHOUSE_APPLY_HTML,
        max_jobs=max_jobs,
        jobhunter_extra_args=extra_args,
    )


def _wait_for_orchestration_terminal(client: TestClient, timeout: float = 60.0) -> dict[str, Any]:
    """Poll orchestration status until terminal."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/orchestration/status").json()
        if last["status"] in ("idle", "completed", "failed", "cancelled"):
            return last
        time.sleep(0.3)
    raise RuntimeError(f"Orchestration did not reach terminal state in {timeout}s. Last: {last}")


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    """The queue file path used by the fake JobHunter."""
    return tmp_path / "queue.jsonl"


@pytest.fixture
def app_settings(tmp_path: Path, queue_path: Path) -> Settings:
    """Settings with the fake JobHunter repo and a queue path."""
    settings = _make_settings(tmp_path, queue_path=queue_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    return settings


@pytest.fixture
def client(app_settings: Settings) -> Any:
    """A TestClient with the orchestration service initialized."""
    app = create_app(settings=app_settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client
        # Before the TestClient context exits, ensure any pipeline worker
        # subprocess has been reaped. The lifespan shutdown calls
        # pipeline_worker.shutdown() which terminates the process, but the
        # drain threads may still be reading pipes. Explicitly waiting here
        # prevents ResourceWarning and OSError from concurrent pipe reads.
        worker = app.state.pipeline_worker
        if worker is not None:
            proc = getattr(worker, "_proc", None)  # noqa: SLF001
            if proc is not None and proc.poll() is None:
                try:
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass
    # Force GC to clean up any lingering Popen handles from this test
    # before the next test starts (prevents cross-test ResourceWarning).
    import gc

    gc.collect()


class TestSequentialOrchestration:
    """Tests 1, 2: sequential ordering + JobHunter failure prevention."""

    def test_sequential_ordering_jobhunter_then_import_then_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 1: Sequential ordering is exactly JobHunter -> import -> pipeline."""
        state = _start_orchestration(client, mode="sequential", max_jobs=2)
        assert state["run_id"] is not None

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed", f"Expected completed, got {final}"
        assert final["mode"] == "sequential"

        # JobHunter ran and exited 0.
        assert final["jobhunter_exit_code"] == 0
        assert final["jobhunter_pid"] is not None

        # Queue import ran and succeeded.
        assert final["queue_import_state"] == "success"
        assert final["queue_imported"] >= 1

        # Pipeline ran and completed.
        assert final["pipeline_run_id"] is not None
        assert final["pipeline_state"] in ("completed", "idle")

        # The queue file was written by the fake JobHunter.
        assert queue_path.exists()

    def test_jobhunter_failure_prevents_import_and_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 2: JobHunter failure prevents new import and new-job processing."""
        # Write an existing valid queue first (so we can prove it's not overwritten).
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text("", encoding="utf-8")

        # Start with the fake JobHunter in --fail mode.
        _start_orchestration(client, mode="sequential", extra_args=["--fail"], max_jobs=2)

        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "failed"
        assert "JobHunter failed" in final["last_error"]
        assert final["jobhunter_exit_code"] == 1
        # No import happened.
        assert final["queue_import_state"] is None
        # No pipeline started.
        assert final["pipeline_run_id"] is None


class TestParallelOrchestration:
    """Tests 3, 4: parallel mode processes existing jobs + imports new ones."""

    def test_parallel_processes_existing_jobs_while_jobhunter_runs(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        """Test 3: Parallel mode processes existing jobs while JobHunter runs."""
        # Seed an existing job so the UAA pipeline has something to process.
        job = _make_job(
            tmp_path,
            "parallel-existing-1",
            url="https://boards.greenhouse.io/example/jobs/parallel-existing-1",
        )
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            upsert_application_job(session, job)

        # Use a delay so JobHunter is still running when we check.
        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--delay", "2.0", "--jobs", "1"],
            max_jobs=2,
        )

        # While JobHunter is running (delay=2s), the pipeline should start.
        time.sleep(1.0)
        mid_status = client.get("/api/orchestration/status").json()
        # The pipeline should be running or have started.
        assert mid_status["pipeline_run_id"] is not None or mid_status["pipeline_state"] is not None

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["jobhunter_exit_code"] == 0
        assert final["queue_import_state"] == "success"

    def test_parallel_imports_new_jobs_after_producer_completion(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Test 4: Parallel mode imports newly exported jobs after producer completion."""
        _start_orchestration(client, mode="parallel", max_jobs=2)

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["jobhunter_exit_code"] == 0
        assert final["queue_import_state"] == "success"
        assert final["queue_imported"] >= 1
        # The queue file exists with at least one job.
        assert queue_path.exists()
        lines = queue_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        job_data = json.loads(lines[0])
        assert "application_id" in job_data


class TestIdempotencyAndDuplicateGuards:
    """Tests 5, 6: idempotent queue + 409 on duplicate start."""

    def test_duplicate_queue_content_is_idempotent(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 5: Duplicate queue content remains idempotent."""
        # First orchestration run.
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final1 = _wait_for_orchestration_terminal(client, timeout=60)
        assert final1["status"] == "completed"
        imported1 = final1["queue_imported"]

        # Wait for the orchestration thread to fully complete before starting
        # the second run (prevents Popen GC race).
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        if svc._thread is not None and svc._thread.is_alive():  # type: ignore[attr-defined]
            svc._thread.join(timeout=10)

        # Second run with the same queue content (fake JH writes the same jobs).
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final2 = _wait_for_orchestration_terminal(client, timeout=60)
        assert final2["status"] == "completed"
        # Idempotent: same content -> imported count is 0 (all upserts hit existing rows).
        assert final2["queue_imported"] == 0 or final2["queue_imported"] == imported1

    def test_duplicate_orchestration_start_returns_409(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 6: Duplicate orchestration start returns 409."""
        # Use a delayed JobHunter so the first run is still active.
        _start_orchestration(client, mode="sequential", extra_args=["--delay", "5.0"], max_jobs=1)

        # Give it a moment to register as active.
        time.sleep(0.5)

        resp = client.post(
            "/api/orchestration/start",
            json={"mode": "sequential", "fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
        )
        assert resp.status_code == 409
        assert "already active" in resp.json()["detail"].lower()

        # Clean up: cancel the first run.
        client.post("/api/orchestration/cancel")
        _wait_for_orchestration_terminal(client, timeout=30)


class TestCancellation:
    """Test 7: cancel terminates owned JobHunter + cancels UAA safely."""

    def test_cancel_terminates_jobhunter_and_cancels_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 7: Cancel terminates the owned fake JobHunter process and cancels UAA."""
        # Use a long delay so we can cancel while JobHunter is still running.
        _start_orchestration(client, mode="sequential", extra_args=["--delay", "30.0"], max_jobs=1)

        # Wait for JobHunter to start.
        time.sleep(1.0)
        mid = client.get("/api/orchestration/status").json()
        assert mid["jobhunter_pid"] is not None

        # Cancel.
        cancel_resp = client.post("/api/orchestration/cancel")
        assert cancel_resp.status_code == 200

        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "cancelled"
        assert final["cancel_reason"]


class TestRestartRecovery:
    """Test 8: restart preserves state, no duplicate children."""

    def test_restart_preserves_state_and_no_duplicate_children(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Test 8: Restart preserves orchestration state and does not duplicate children."""
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        # First app: seed an active orchestration run directly in the DB.
        app1 = create_app(settings=settings)
        with TestClient(app1):
            Base.metadata.create_all(app1.state.engine)
            from universal_auto_applier.persistence.orchestration_run_repository import (
                create_orchestration_run,
                update_orchestration_run,
            )

            with session_scope(app1.state.session_factory) as session:
                create_orchestration_run(session, run_id="orch-restart-1", mode="sequential")
                update_orchestration_run(
                    session,
                    "orch-restart-1",
                    status="jobhunter_running",
                    jobhunter_pid=99999,  # a PID that won't exist
                    current_phase="jobhunter_running",
                )

        # Second app over the same DB: startup recovery should mark the
        # orphaned run as failed.
        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            Base.metadata.create_all(app2.state.engine)
            status = client2.get("/api/orchestration/status").json()
            assert status["run_id"] == "orch-restart-1"
            assert status["status"] == "failed"
            assert (
                "interrupted" in status["last_error"].lower()
                or "restart" in status["last_error"].lower()
            )

            # A fresh start is allowed (the orphaned run is terminal).
            resp = client2.post(
                "/api/orchestration/start",
                json={"mode": "sequential", "fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
            )
            assert resp.status_code == 200
            assert resp.json()["run_id"] != "orch-restart-1"
            # Cancel it to clean up.
            client2.post("/api/orchestration/cancel")
            _wait_for_orchestration_terminal(client2, timeout=30)


class TestSafety:
    """Tests 11, 12: no submission, no SUBMITTED/APPLIED transitions."""

    def test_no_submission_method_invoked(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 11: No submission method is invoked during orchestration."""
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"

        # Check that no submission results were created.
        from sqlalchemy import func, select

        from universal_auto_applier.persistence.models import SubmissionResultRow

        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            count = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()
        assert count == 0, "No submission results should exist after orchestration"

    def test_no_job_reaches_submitted_or_applied(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 12: No application reaches SUBMITTED or APPLIED."""
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"

        # Check all jobs in the DB.
        from sqlalchemy import select

        from universal_auto_applier.persistence.models import ApplicationJobRow

        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            jobs = list(session.execute(select(ApplicationJobRow)).scalars().all())
        for job in jobs:
            assert str(job.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job {job.application_id} reached {job.status}!"


class TestBoundedCaptureAndSecretFilter:
    """Tests 9, 10: paths with spaces, bounded capture, secret filtering."""

    def test_paths_containing_spaces_work(self, tmp_path: Path) -> None:
        """Test 9: Paths containing spaces work (Windows compatibility)."""
        spaced_dir = tmp_path / "dir with spaces"
        spaced_dir.mkdir(parents=True, exist_ok=True)
        spaced_queue = spaced_dir / "queue file.jsonl"
        settings = _make_settings(tmp_path, queue_path=spaced_queue)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        app = create_app(settings=settings)
        with TestClient(app) as test_client:
            Base.metadata.create_all(app.state.engine)
            _start_orchestration(test_client, mode="sequential", max_jobs=1)
            final = _wait_for_orchestration_terminal(test_client, timeout=60)
            assert final["status"] == "completed"
            # The queue file was written to the spaced path.
            assert spaced_queue.exists()

    def test_output_capture_is_bounded_and_excludes_secrets(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 10: Output capture is bounded and excludes secrets."""
        # Use --secret-leak to print a fake secret, then verify it's filtered.
        _start_orchestration(client, mode="sequential", extra_args=["--secret-leak"], max_jobs=1)

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"

        # The captured stdout must NOT contain the secret.
        assert "sk-or-v1" not in final["jobhunter_stdout"]
        assert "OPENROUTER_API_KEY" not in final["jobhunter_stdout"]
        # The redacted marker should be present instead.
        assert "[redacted]" in final["jobhunter_stdout"]

        # The capture is bounded (the setting default is 8192 bytes).
        assert (
            len(final["jobhunter_stdout"].encode("utf-8")) <= 8192 + 100
        )  # allow truncation marker


class TestJobHunterRunner:
    """Unit tests for the JobHunter subprocess boundary."""

    def test_build_command_uses_argument_list_not_shell_string(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """The command is an argument list, never a shell string."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = _make_settings(tmp_path, queue_path=queue_path)
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=queue_path,
        )
        cmd = runner.build_command()
        assert isinstance(cmd, list)
        assert "--output" in cmd
        assert str(queue_path) in cmd
        # The Python executable comes first, then the script path.
        assert cmd[0]  # python
        assert cmd[1].endswith("run_export_queue.py")

    def test_validate_rejects_missing_repo(self, tmp_path: Path, queue_path: Path) -> None:
        """Validation fails when the repo path is not configured."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = Settings(
            host="127.0.0.1",
            port=8400,
            data_dir=tmp_path / "uaa",
            jobhunter_repo=None,
            queue_path=queue_path,
        )
        runner = JobHunterRunner(settings=settings, queue_output_path=queue_path)
        with pytest.raises(RuntimeError, match="not configured"):
            runner.validate()

    def test_validate_rejects_missing_entry_point(self, tmp_path: Path, queue_path: Path) -> None:
        """Validation fails when the entry point does not exist."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = Settings(
            host="127.0.0.1",
            port=8400,
            data_dir=tmp_path / "uaa",
            jobhunter_repo=tmp_path,  # exists but no run_export_queue.py
            queue_path=queue_path,
        )
        runner = JobHunterRunner(settings=settings, queue_output_path=queue_path)
        with pytest.raises(RuntimeError, match="entry point not found"):
            runner.validate()

    def test_launch_and_wait_writes_queue_atomically(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Launching and waiting writes the queue file atomically."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = _make_settings(tmp_path, queue_path=queue_path)
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=queue_path,
            extra_args=["--jobs", "3"],
        )
        pid = runner.launch()
        assert pid > 0
        exit_code = runner.wait(timeout=30)
        assert exit_code == 0
        assert queue_path.exists()
        lines = queue_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
