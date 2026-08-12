"""Integration tests for WQ-6 cross-repository orchestration.

These tests prove the orchestration service coordinates JobHunter export ->
UAA queue import -> UAA pipeline correctly in both sequential and parallel
modes, with durable state, safe cancellation, restart recovery, and no
final submission.

All tests use a local fake JobHunter script (tests/fixtures/fake_jobhunter/)
and local fixture HTML. No real ATS, no public web, no real submission.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

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
from universal_auto_applier.persistence.models import (
    ApplicationJobRow,
    Base,
    SubmissionResultRow,
)
from universal_auto_applier.services.pipeline_recovery_service import pid_is_alive

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "platforms" / "greenhouse_apply.html").read_text(
    encoding="utf-8"
)
FAKE_JH_REPO = FIXTURES_DIR / "fake_jobhunter"


def _make_settings(
    tmp_path: Path,
    *,
    queue_path: Path | None = None,
    mode: str = "sequential",
    entry_point: str = "run_all.py",
    timeout_seconds: int = 0,
) -> Settings:
    """Build settings pointing at the fake JobHunter repo.

    The default entry point is ``run_all.py`` (the fake full-workflow
    producer). For tests that need the standalone exporter with ``--output``,
    set ``entry_point="run_export_queue.py"``.
    """
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
        jobhunter_entry_point=entry_point,
        queue_path=queue_path,
        orchestration_mode=mode,
        jobhunter_timeout_seconds=timeout_seconds,
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
    """The queue file path used by the fake JobHunter.

    For ``run_all.py``, the queue is written to ``data/application_queue.jsonl``
    relative to the fake JH repo root. For ``run_export_queue.py``, it's the
    explicit ``--output`` path.
    """
    return FAKE_JH_REPO / "data" / "application_queue.jsonl"


@pytest.fixture
def app_settings(tmp_path: Path, queue_path: Path) -> Settings:
    """Settings with the fake JobHunter repo and a queue path."""
    # Clean any stale queue/PDF files from the fake JH repo.
    data_dir = FAKE_JH_REPO / "data"
    if data_dir.exists():
        for f in data_dir.iterdir():
            if f.suffix in (".jsonl", ".pdf") or f.name.startswith(".uaa"):
                f.unlink()
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
        # Properly shut down the orchestration service (cancels JobHunter,
        # waits for the orchestration thread, ensures Popen is reaped).
        orch = getattr(app.state, "orchestration_service", None)
        if orch is not None and hasattr(orch, "shutdown"):
            orch.shutdown()
        # Properly shut down the pipeline worker (terminates subprocess,
        # waits, joins drain threads, closes pipes).
        worker = app.state.pipeline_worker
        if worker is not None and hasattr(worker, "shutdown"):
            worker.shutdown()
        else:
            # Fallback: ensure any lingering subprocess is reaped.
            proc = getattr(worker, "_proc", None)  # noqa: SLF001
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass


class TestSequentialOrchestration:
    """Tests 1, 2: sequential ordering + JobHunter failure prevention."""

    def test_sequential_full_workflow_ordering_jobhunter_then_import_then_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 1: Sequential ordering with full-workflow producer.

        Proves the exact order: scan → evaluate/tailor → atomic export →
        queue import → UAA pipeline. The fake ``run_all.py`` emits phase
        evidence (SCAN, EVAL, EXPORT) in its stdout, which is captured.
        """
        state = _start_orchestration(client, mode="sequential", max_jobs=2)
        assert state["run_id"] is not None

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed", f"Expected completed, got {final}"
        assert final["mode"] == "sequential"

        # JobHunter ran and exited 0.
        assert final["jobhunter_exit_code"] == 0
        assert final["jobhunter_pid"] is not None

        # The captured stdout contains phase evidence from the full workflow.
        stdout = final["jobhunter_stdout"]
        assert "[SCAN]" in stdout, f"Expected SCAN phase evidence in stdout, got: {stdout}"
        assert "[EVAL]" in stdout, f"Expected EVAL phase evidence in stdout, got: {stdout}"
        assert "[EXPORT]" in stdout, f"Expected EXPORT phase evidence in stdout, got: {stdout}"

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
        job = _make_job(
            tmp_path,
            "parallel-existing-1",
            url="https://boards.greenhouse.io/example/jobs/parallel-existing-1",
        )
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            upsert_application_job(session, job)

        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--delay", "2.0", "--jobs", "1"],
            max_jobs=2,
        )

        # While JobHunter is running (delay=2s), the pipeline should start.
        # On Windows, the pipeline subprocess may take longer to launch.
        # Poll for up to 5s instead of a single sleep+check.
        deadline = time.monotonic() + 5.0
        started = False
        while time.monotonic() < deadline:
            mid_status = client.get("/api/orchestration/status").json()
            if (
                mid_status["pipeline_run_id_initial"] is not None
                or mid_status["pipeline_state_initial"] is not None
            ):
                started = True
                break
            time.sleep(0.3)
        assert started, f"Pipeline did not start while JobHunter was running. Status: {mid_status}"

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed"
        assert final["jobhunter_exit_code"] == 0
        assert final["queue_import_state"] == "success"

    def test_parallel_second_pass_for_newly_imported_jobs(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Test 4: Parallel mode starts a second pipeline pass for newly imported jobs.

        Proves:
        - The initial pipeline pass runs (pipeline_run_id_initial is set).
        - After import, newly eligible jobs trigger a second pass
          (pipeline_run_id is set and different from pipeline_run_id_initial).
        - The second pass reaches a terminal state.
        - The newly exported job reaches a valid review terminal state.
        """
        # Clean any previous queue so the fake producer writes fresh jobs.
        if queue_path.exists():
            queue_path.unlink()

        _start_orchestration(client, mode="parallel", max_jobs=2)

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed", f"Expected completed, got {final}"
        assert final["jobhunter_exit_code"] == 0
        assert final["queue_import_state"] == "success"
        assert final["queue_imported"] >= 1

        # The initial pipeline pass was recorded.
        assert final["pipeline_run_id_initial"] is not None

        # The second pipeline pass was started (for newly imported jobs).
        assert final["pipeline_run_id"] is not None
        assert final["pipeline_run_id"] != final["pipeline_run_id_initial"]

        # Verify the newly imported job reached a valid review terminal state.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            jobs = list(session.execute(select(ApplicationJobRow)).scalars().all())
        for job in jobs:
            assert str(job.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job {job.application_id} reached {job.status}!"
            # Valid terminal states: review_ready, needs_user_input, failed.
            assert str(job.status) in (
                ApplicationStatus.REVIEW_READY.value,
                ApplicationStatus.NEEDS_USER_INPUT.value,
                ApplicationStatus.FAILED.value,
            ), f"Job {job.application_id} is in non-terminal state {job.status}"

    def test_parallel_no_second_pass_when_zero_new_eligible(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        """Test 4b: No second pipeline pass when import produced zero new eligible jobs.

        Seed the same job that the fake producer will export, so the import
        is a no-op (all upserts hit existing rows). Proves no second pass starts.
        """
        # Pre-seed the same job the fake producer will write.
        external_id = "fake-jh-job-0"
        url = f"https://boards.greenhouse.io/example/jobs/{external_id}"
        job = _make_job(tmp_path, external_id, url=url)
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            upsert_application_job(session, job)

        _start_orchestration(client, mode="parallel", max_jobs=2)

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed"
        # No newly eligible jobs → no second pass.
        assert final["pipeline_run_id"] is None
        # But the initial pass ran.
        assert final["pipeline_run_id_initial"] is not None


class TestIdempotencyAndDuplicateGuards:
    """Tests 5, 6: idempotent queue + 409 on duplicate start."""

    def test_duplicate_queue_content_is_strictly_idempotent(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 5: Duplicate queue content is strictly idempotent.

        Proves exactly:
        - no duplicate application rows (job count unchanged);
        - no second post-import pipeline pass;
        - no duplicate processing of the same application (no new submission results).
        """
        # First orchestration run.
        _start_orchestration(client, mode="parallel", max_jobs=2)
        final1 = _wait_for_orchestration_terminal(client, timeout=90)
        assert final1["status"] == "completed"

        # Count jobs and submission results after first run.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            job_count_after_first = session.execute(
                select(func.count()).select_from(ApplicationJobRow)
            ).scalar_one()
            submission_count_after_first = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        # Wait for the orchestration thread to fully complete.
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        if svc._thread is not None and svc._thread.is_alive():  # type: ignore[attr-defined]
            svc._thread.join(timeout=10)

        # Second run with the same queue content (fake JH writes the same jobs).
        _start_orchestration(client, mode="parallel", max_jobs=2)
        final2 = _wait_for_orchestration_terminal(client, timeout=90)
        assert final2["status"] == "completed"

        # No duplicate application rows: job count is unchanged.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            job_count_after_second = session.execute(
                select(func.count()).select_from(ApplicationJobRow)
            ).scalar_one()
            submission_count_after_second = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()
        assert job_count_after_second == job_count_after_first, (
            f"Expected {job_count_after_first} jobs after second run, got {job_count_after_second}"
        )

        # No second post-import pipeline pass (no newly eligible jobs since
        # the first run already processed them to terminal states).
        assert final2["pipeline_run_id"] is None, (
            f"Expected no second pipeline pass, got {final2['pipeline_run_id']}"
        )

        # No duplicate processing: no new submission results.
        assert submission_count_after_second == submission_count_after_first, (
            f"Expected {submission_count_after_first} submissions, "
            f"got {submission_count_after_second}"
        )

    def test_duplicate_orchestration_start_returns_409(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 6: Duplicate orchestration start returns 409."""
        _start_orchestration(client, mode="sequential", extra_args=["--delay", "5.0"], max_jobs=1)

        time.sleep(0.5)

        resp = client.post(
            "/api/orchestration/start",
            json={"mode": "sequential", "fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
        )
        assert resp.status_code == 409
        assert "already active" in resp.json()["detail"].lower()

        client.post("/api/orchestration/cancel")
        _wait_for_orchestration_terminal(client, timeout=30)


class TestCancellation:
    """Test 7: cancel terminates owned JobHunter + cancels UAA safely."""

    def test_cancel_terminates_jobhunter_and_cancels_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 7: Cancel terminates the owned fake JobHunter process and cancels UAA."""
        _start_orchestration(client, mode="sequential", extra_args=["--delay", "30.0"], max_jobs=1)

        time.sleep(1.0)
        mid = client.get("/api/orchestration/status").json()
        assert mid["jobhunter_pid"] is not None
        pid = mid["jobhunter_pid"]

        cancel_resp = client.post("/api/orchestration/cancel")
        assert cancel_resp.status_code == 200

        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "cancelled"
        assert final["cancel_reason"]

        # The JobHunter child PID is no longer alive.
        # Use pid_is_alive which works on both Unix and Windows.
        assert not pid_is_alive(pid), f"JobHunter PID {pid} is still alive after cancel"


class TestRestartRecovery:
    """Test 8: restart preserves state, no duplicate children."""

    def test_restart_preserves_state_and_no_duplicate_children(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Test 8: Restart preserves orchestration state and does not duplicate children."""
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

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
                    jobhunter_pid=99999,
                    current_phase="jobhunter_running",
                )

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

            resp = client2.post(
                "/api/orchestration/start",
                json={"mode": "sequential", "fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
            )
            assert resp.status_code == 200
            assert resp.json()["run_id"] != "orch-restart-1"
            client2.post("/api/orchestration/cancel")
            _wait_for_orchestration_terminal(client2, timeout=30)


class TestTimeoutCleanup:
    """Test 3 (defect 3): timeout cleanup."""

    def test_timeout_terminates_child_no_import_no_pipeline(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Test: JobHunter timeout terminates the child, no import, no pipeline.

        Uses a short timeout (2s) with a fake producer that sleeps 60s.
        Proves:
        - the child PID is no longer alive;
        - no queue is imported;
        - no pipeline starts;
        - the orchestration run is marked failed.
        """
        settings = _make_settings(
            tmp_path, queue_path=queue_path, mode="sequential", timeout_seconds=2
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            svc = client.app.state.orchestration_service  # type: ignore[union-attr]
            svc.start(
                mode="sequential",
                fixture_html=GREENHOUSE_APPLY_HTML,
                max_jobs=1,
                jobhunter_extra_args=["--timeout-test"],
            )

            # Wait for the run to reach a terminal state.
            time.sleep(1.0)
            mid = client.get("/api/orchestration/status").json()
            assert mid["jobhunter_pid"] is not None
            pid = mid["jobhunter_pid"]

            final = _wait_for_orchestration_terminal(client, timeout=30)
            assert final["status"] == "failed"
            assert "timed out" in final["last_error"].lower()

            # The child PID is no longer alive (cross-platform check).
            assert not pid_is_alive(pid), f"JobHunter PID {pid} is still alive after timeout"

            # No queue import occurred.
            assert final["queue_import_state"] is None
            # No pipeline started.
            assert final["pipeline_run_id"] is None


class TestHighVolumeOutput:
    """Test 4 (defect 4): high-volume output / pipe deadlock prevention."""

    def test_high_volume_output_no_deaddown(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test: High-volume stdout/stderr does not cause deadlock.

        The fake producer writes 256KB to both stdout and stderr (well above
        the typical 64KB OS pipe buffer). Proves:
        - the process exits (no deadlock);
        - stored output is within configured bounds;
        - secrets are absent (if --secret-leak is also used);
        - no ResourceWarnings occur (enforced by pytest config).
        """
        _start_orchestration(
            client,
            mode="sequential",
            extra_args=["--volume", "262144", "--secret-leak"],
            max_jobs=1,
        )

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["jobhunter_exit_code"] == 0

        # Stored output is within bounds.
        max_bytes = 8192 + 100  # allow truncation marker
        assert len(final["jobhunter_stdout"].encode("utf-8")) <= max_bytes
        assert len(final["jobhunter_stderr"].encode("utf-8")) <= max_bytes

        # Secrets are absent.
        assert "sk-or-v1" not in final["jobhunter_stdout"]
        assert "OPENROUTER_API_KEY" not in final["jobhunter_stdout"]
        assert "[redacted]" in final["jobhunter_stdout"]


class TestSafety:
    """Tests 11, 12: no submission, no SUBMITTED/APPLIED transitions."""

    def test_no_submission_method_invoked(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 11: No submission method is invoked during orchestration."""
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"

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

    def test_output_capture_is_bounded_and_excludes_secrets(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        """Test 10: Output capture is bounded and excludes secrets."""
        _start_orchestration(client, mode="sequential", extra_args=["--secret-leak"], max_jobs=1)

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"

        assert "sk-or-v1" not in final["jobhunter_stdout"]
        assert "OPENROUTER_API_KEY" not in final["jobhunter_stdout"]
        assert "[redacted]" in final["jobhunter_stdout"]
        assert len(final["jobhunter_stdout"].encode("utf-8")) <= 8192 + 100


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
        # run_all.py does NOT get --output.
        assert "--output" not in cmd
        # The Python executable comes first, then the script path.
        assert cmd[0]  # python
        assert cmd[1].endswith("run_all.py")

    def test_build_command_export_queue_passes_output(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """run_export_queue.py DOES get --output."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = _make_settings(
            tmp_path, queue_path=queue_path, entry_point="run_export_queue.py"
        )
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=queue_path,
            entry_point="run_export_queue.py",
        )
        cmd = runner.build_command()
        assert "--output" in cmd
        assert str(queue_path) in cmd

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
            jobhunter_repo=tmp_path,
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

        settings = _make_settings(
            tmp_path, queue_path=queue_path, entry_point="run_export_queue.py"
        )
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=queue_path,
            entry_point="run_export_queue.py",
            extra_args=["--jobs", "3"],
        )
        pid = runner.launch()
        assert pid > 0
        exit_code = runner.wait(timeout=30)
        assert exit_code == 0
        assert queue_path.exists()
        lines = queue_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_timeout_terminates_child_and_reaps(self, tmp_path: Path, queue_path: Path) -> None:
        """Test: JobHunterRunner.wait() timeout terminates the child and reaps it."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = _make_settings(
            tmp_path,
            queue_path=queue_path,
            entry_point="run_export_queue.py",
            timeout_seconds=2,
        )
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=queue_path,
            entry_point="run_export_queue.py",
            extra_args=["--timeout-test"],
        )
        pid = runner.launch()
        runner.wait(timeout=2)
        # The process was terminated.
        assert runner.was_timed_out
        # The PID is no longer alive (cross-platform check).
        assert not pid_is_alive(pid), f"PID {pid} still alive after timeout"


class TestRestartLivenessSafety:
    """Test 6 (defect 6): restart/liveness safety."""

    def test_dead_persisted_jobhunter_pid_recovered_safely(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """A dead persisted JobHunter PID is recovered safely on restart."""
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        # Use a PID that is guaranteed to not exist (very high number).
        dead_pid = 999999

        app1 = create_app(settings=settings)
        with TestClient(app1):
            Base.metadata.create_all(app1.state.engine)
            from universal_auto_applier.persistence.orchestration_run_repository import (
                create_orchestration_run,
                update_orchestration_run,
            )

            with session_scope(app1.state.session_factory) as session:
                create_orchestration_run(session, run_id="orch-dead-pid-1", mode="sequential")
                update_orchestration_run(
                    session,
                    "orch-dead-pid-1",
                    status="jobhunter_running",
                    jobhunter_pid=dead_pid,
                    current_phase="jobhunter_running",
                )

        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            Base.metadata.create_all(app2.state.engine)
            status = client2.get("/api/orchestration/status").json()
            assert status["status"] == "failed"
            assert "interrupted" in status["last_error"].lower()

    def test_live_child_not_treated_as_dead(self, tmp_path: Path, queue_path: Path) -> None:
        """A confirmed-live child is not treated as dead and blocks duplicate start.

        This test seeds an orchestration run with the CURRENT process's PID
        as the JobHunter PID. Since the current process is alive, the
        startup recovery should NOT mark it as failed. The run remains active
        and blocks a new orchestration start with 409.
        """
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        live_pid = os.getpid()

        app1 = create_app(settings=settings)
        with TestClient(app1):
            Base.metadata.create_all(app1.state.engine)
            from universal_auto_applier.persistence.orchestration_run_repository import (
                create_orchestration_run,
                update_orchestration_run,
            )

            with session_scope(app1.state.session_factory) as session:
                create_orchestration_run(session, run_id="orch-live-pid-1", mode="sequential")
                update_orchestration_run(
                    session,
                    "orch-live-pid-1",
                    status="jobhunter_running",
                    jobhunter_pid=live_pid,
                    current_phase="jobhunter_running",
                )

        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            Base.metadata.create_all(app2.state.engine)
            status = client2.get("/api/orchestration/status").json()
            # The run is still active (not recovered).
            assert status["status"] == "jobhunter_running"
            # A new start is blocked with 409.
            resp = client2.post(
                "/api/orchestration/start",
                json={"mode": "sequential", "fixture_html": GREENHOUSE_APPLY_HTML, "max_jobs": 1},
            )
            assert resp.status_code == 409

    def test_restart_never_launches_replacement_child(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Restart never launches a replacement JobHunter child automatically."""
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        # Seed an active orchestration run.
        app1 = create_app(settings=settings)
        with TestClient(app1):
            Base.metadata.create_all(app1.state.engine)
            from universal_auto_applier.persistence.orchestration_run_repository import (
                create_orchestration_run,
                update_orchestration_run,
            )

            with session_scope(app1.state.session_factory) as session:
                create_orchestration_run(session, run_id="orch-no-auto-1", mode="sequential")
                update_orchestration_run(
                    session,
                    "orch-no-auto-1",
                    status="jobhunter_running",
                    jobhunter_pid=999999,
                    current_phase="jobhunter_running",
                )

        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            Base.metadata.create_all(app2.state.engine)
            status = client2.get("/api/orchestration/status").json()
            # The run was recovered (marked failed), not auto-retried.
            assert status["status"] == "failed"
            # No new JobHunter PID was launched.
            status2 = client2.get("/api/orchestration/status").json()
            assert status2["jobhunter_pid"] == 999999  # unchanged

    def test_cancel_never_kills_using_only_stale_pid(
        self, tmp_path: Path, queue_path: Path
    ) -> None:
        """Cancellation never kills a process using only an unverified persisted PID.

        The orchestration service's cancel() uses the owned Popen handle,
        never a stale PID from the database. This test verifies that when
        there's no owned runner (e.g. after a restart), cancel() does not
        attempt to kill the persisted PID.
        """
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))

        # Use the current process's PID as the persisted JobHunter PID.
        # If cancel() tried to kill it using the stale PID, it would kill
        # the test process itself.
        live_pid = os.getpid()

        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            from universal_auto_applier.persistence.orchestration_run_repository import (
                create_orchestration_run,
                update_orchestration_run,
            )

            with session_scope(app.state.session_factory) as session:
                create_orchestration_run(session, run_id="orch-cancel-stale-1", mode="sequential")
                update_orchestration_run(
                    session,
                    "orch-cancel-stale-1",
                    status="jobhunter_running",
                    jobhunter_pid=live_pid,
                    current_phase="jobhunter_running",
                )

            # Cancel should NOT kill the persisted PID (the test process).
            resp = client.post("/api/orchestration/cancel")
            assert resp.status_code == 200

            # The test process is still alive (we're still running).
            assert os.getpid() == live_pid
