"""Final behavioral tests for WQ-6 cross-repository orchestration.

These tests prove the orchestration service's final behavioral contract:

- A failed second pipeline pass fails the whole orchestration run while
  leaving imported jobs safely eligible for manual retry (no submission).
- Queue publication is detected via content hash + mtime, distinguishing a
  freshly published queue from a stale pre-existing file.
- The exact set of newly eligible application IDs is persisted and is
  idempotent across re-imports.
- A newly exported job completes its full lifecycle: import -> second
  pipeline pass -> terminal review state, with both pipeline run IDs
  persisted and no SUBMITTED/APPLIED transition.

All tests use a local fake JobHunter script (tests/fixtures/fake_jobhunter/)
and local fixture HTML. No real ATS, no public web, no real submission.
"""

from __future__ import annotations

import hashlib
import json
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
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import (
    ApplicationJobRow,
    Base,
    SubmissionResultRow,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "platforms" / "greenhouse_apply.html").read_text(
    encoding="utf-8"
)
FAKE_JH_REPO = FIXTURES_DIR / "fake_jobhunter"


# ---------------------------------------------------------------------------
# Helpers (self-contained copies of the helpers in test_orchestration.py)
# ---------------------------------------------------------------------------


def _make_settings(
    tmp_path: Path,
    *,
    queue_path: Path | None = None,
    mode: str = "sequential",
    entry_point: str = "run_all.py",
    timeout_seconds: int = 0,
) -> Settings:
    """Build settings pointing at the fake JobHunter repo."""
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq6_final",
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
    """Build a minimal ApplicationJob for pre-seeding the DB."""
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


def _wait_for_orchestration_terminal(client: TestClient, timeout: float = 90.0) -> dict[str, Any]:
    """Poll orchestration status until terminal."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/orchestration/status").json()
        if last["status"] in ("idle", "completed", "failed", "cancelled"):
            return last
        time.sleep(0.3)
    raise RuntimeError(f"Orchestration did not reach terminal state in {timeout}s. Last: {last}")


def _clean_fake_jh_data(queue_path: Path) -> None:
    """Delete the queue file and any PDF files in the fake JH data directory.

    Tests that need a fresh publication (no stale queue from a previous test)
    call this before starting orchestration.
    """
    if queue_path.exists():
        queue_path.unlink()
    data_dir = queue_path.parent
    if data_dir.exists():
        for pdf in data_dir.glob("*.pdf"):
            try:
                pdf.unlink()
            except OSError:
                pass


def _write_valid_queue_file(queue_path: Path, num_jobs: int = 1) -> list[dict[str, Any]]:
    """Write a valid queue file with the given number of jobs.

    Creates the dummy PDF files referenced by each queue entry so the
    importer's existence check passes for ``ready_to_apply`` jobs. Returns
    the list of job dicts written.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir = queue_path.parent.resolve()
    jobs: list[dict[str, Any]] = []
    for i in range(num_jobs):
        external_id = f"fake-jh-job-{i}"
        platform = "greenhouse"
        url = f"https://boards.greenhouse.io/example/jobs/{external_id}"
        application_id = hashlib.sha256(f"{platform}:{external_id}".encode()).hexdigest()
        cv_path = output_dir / f"cv-{i}.pdf"
        cover_path = output_dir / f"cover-{i}.pdf"
        cv_path.write_bytes(b"%PDF fake cv")
        cover_path.write_bytes(b"%PDF fake cover")
        jobs.append(
            {
                "application_id": application_id,
                "platform": platform,
                "external_job_id": external_id,
                "source": "fake_jobhunter",
                "company": f"Fake Corp {i}",
                "title": f"Software Engineer {i}",
                "url": url,
                "verdict": "apply",
                "status": "ready_to_apply",
                "score": 4.0 + (i * 0.1),
                "cv_pdf": str(cv_path),
                "cover_letter_pdf": str(cover_path),
                "metadata": {
                    "candidate_profile": {
                        "first_name": "Test",
                        "last_name": "User",
                        "full_name": "Test User",
                        "email": "test@example.com",
                        "phone": "+49 123",
                        "requires_sponsorship": False,
                    },
                },
            }
        )
    lines = [json.dumps(job, separators=(",", ":")) for job in jobs]
    queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jobs


def _hash_file_bytes(path: Path) -> str | None:
    """Return the SHA-256 hex digest of the file's raw bytes, or None if missing."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_queue_application_ids(queue_path: Path) -> list[str]:
    """Parse the queue file and return the list of application_id values."""
    if not queue_path.exists():
        return []
    ids: list[str] = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and "application_id" in parsed:
            ids.append(str(parsed["application_id"]))
    return ids


# ---------------------------------------------------------------------------
# Fake pipeline worker that fails on the second start() call
# ---------------------------------------------------------------------------


class _FailingSecondPassPipelineWorker:
    """Wraps the real PipelineWorkerService; raises on the second ``start()``.

    The orchestration service calls ``start()`` once for the initial pass
    (existing jobs) and once for the second pass (newly imported jobs). This
    wrapper lets the first call succeed and raises ``RuntimeError`` on the
    second, simulating a second-pass failure. All other methods delegate to
    the wrapped real worker.
    """

    def __init__(self, real_worker: Any) -> None:
        self._real = real_worker
        self._start_count = 0

    def start(
        self,
        *,
        max_jobs: int = 10,
        fixture_html: str | None = None,
        target_application_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._start_count += 1
        if self._start_count == 2:
            raise RuntimeError("second pipeline pass failed (simulated)")
        return self._real.start(max_jobs=max_jobs, fixture_html=fixture_html)

    def get_state_dict(self) -> dict[str, Any]:
        return self._real.get_state_dict()

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        return self._real.cancel(reason=reason)

    def shutdown(self) -> None:
        self._real.shutdown()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    """The queue file path used by the fake JobHunter's run_all.py."""
    return FAKE_JH_REPO / "data" / "application_queue.jsonl"


@pytest.fixture
def app_settings(tmp_path: Path, queue_path: Path) -> Settings:
    """Settings with the fake JobHunter repo and a queue path."""
    _clean_fake_jh_data(queue_path)
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
        # Properly shut down all services to prevent ResourceWarning.
        orch = getattr(app.state, "orchestration_service", None)
        if orch is not None and hasattr(orch, "shutdown"):
            orch.shutdown()
        worker = app.state.pipeline_worker
        if worker is not None and hasattr(worker, "shutdown"):
            worker.shutdown()
        else:
            proc = getattr(worker, "_proc", None)  # noqa: SLF001
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestSecondPassFailure:
    """A failed second pipeline pass fails the orchestration run safely."""

    def test_second_pass_failure_fails_orchestration(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A second-pass pipeline failure marks the run failed; jobs stay safe.

        Installs a wrapper around the real PipelineWorkerService that succeeds
        on the first ``start()`` (initial pass) and raises ``RuntimeError`` on
        the second ``start()`` (second pass for newly imported jobs). Proves:

        - final status == "failed";
        - last_error contains "second pipeline pass failed";
        - the imported job remains in a safe pre-submission state
          (READY_TO_APPLY or QUEUED), never SUBMITTED/APPLIED;
        - no SubmissionResultRow rows were created.
        """
        # Clean any previous queue so the fake producer writes exactly one job.
        _clean_fake_jh_data(queue_path)

        # Install the failing-second-pass wrapper on the orchestration service.
        real_worker = client.app.state.pipeline_worker  # type: ignore[union-attr]
        wrapper = _FailingSecondPassPipelineWorker(real_worker)
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        svc._pipeline_worker = wrapper  # type: ignore[attr-defined]

        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "1"],
            max_jobs=2,
        )

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "failed", f"Expected failed, got: {final}"
        assert "second pipeline pass failed" in final["last_error"], (
            f"Expected 'second pipeline pass failed' in last_error, got: {final['last_error']}"
        )

        # The import succeeded (one newly eligible job), but the second pass
        # failed to start.
        assert final["queue_import_state"] == "success"
        assert final["newly_eligible_count"] == 1

        # The newly imported job remains in a safe pre-submission state.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            jobs = list(session.execute(select(ApplicationJobRow)).scalars().all())
            submission_count = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        assert len(jobs) == 1, f"Expected 1 imported job, got {len(jobs)}"
        job = jobs[0]
        assert str(job.status) not in (
            ApplicationStatus.SUBMITTED.value,
            ApplicationStatus.APPLIED.value,
        ), f"Imported job reached unsafe state {job.status}!"
        # The second pass never ran, so the job should still be READY_TO_APPLY
        # (the state it was imported in). Accept QUEUED as a safe variant too.
        assert str(job.status) in (
            ApplicationStatus.READY_TO_APPLY.value,
            ApplicationStatus.QUEUED.value,
        ), f"Imported job is in unexpected state {job.status}"

        # No submission results were created.
        assert submission_count == 0, f"Expected 0 submission results, got {submission_count}"


class TestQueuePublicationDetection:
    """Queue publication is detected via content hash + mtime."""

    def test_pre_existing_queue_no_export_zero_imports(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A pre-existing queue that JobHunter did not touch is not imported.

        Pre-writes a valid queue file, then runs JobHunter with ``--no-export``
        (it succeeds but does not write the queue). The orchestration detects
        that the queue was not published (mtime unchanged) and skips import
        and pipeline entirely.
        """
        _clean_fake_jh_data(queue_path)
        _write_valid_queue_file(queue_path, num_jobs=1)

        _start_orchestration(
            client,
            mode="sequential",
            extra_args=["--no-export"],
            max_jobs=2,
        )

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed", f"Expected completed, got: {final}"
        assert final["queue_published"] is False, (
            f"Expected queue_published False, got: {final['queue_published']}"
        )
        assert final["queue_import_state"] is None, (
            f"Expected no import, got state: {final['queue_import_state']}"
        )
        # No pipeline started.
        assert final["pipeline_run_id"] is None

    def test_newly_published_queue_recognized(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A freshly published queue is recognized and imported.

        No pre-existing queue. JobHunter writes the queue atomically. The
        orchestration detects the publication (file appeared) and imports it.
        """
        _clean_fake_jh_data(queue_path)

        _start_orchestration(client, mode="sequential", max_jobs=2)

        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed", f"Expected completed, got: {final}"
        assert final["queue_published"] is True, (
            f"Expected queue_published True, got: {final['queue_published']}"
        )
        assert final["queue_import_state"] == "success", (
            f"Expected import success, got: {final['queue_import_state']}"
        )
        assert final["queue_imported"] >= 1

    def test_failed_producer_leaves_previous_queue(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A failed producer leaves a pre-existing queue untouched; no import.

        Pre-writes a valid queue, then runs JobHunter with ``--fail`` (exits 1
        during the evaluate phase, before export). The orchestration detects
        the failure and does not import or start the pipeline. The queue
        file's bytes are unchanged.
        """
        _clean_fake_jh_data(queue_path)
        _write_valid_queue_file(queue_path, num_jobs=1)

        hash_before = _hash_file_bytes(queue_path)
        assert hash_before is not None

        _start_orchestration(
            client,
            mode="sequential",
            extra_args=["--fail"],
            max_jobs=2,
        )

        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "failed", f"Expected failed, got: {final}"
        assert "JobHunter failed" in final["last_error"]

        # The queue file's bytes are unchanged.
        hash_after = _hash_file_bytes(queue_path)
        assert hash_after == hash_before, (
            f"Queue file bytes changed: before={hash_before}, after={hash_after}"
        )

        # No import occurred.
        assert final["queue_import_state"] is None
        # No pipeline started.
        assert final["pipeline_run_id"] is None


class TestExactNewlyEligible:
    """The exact set of newly eligible application IDs is persisted."""

    def test_newly_eligible_count_persisted(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Parallel orchestration with one new job persists newly_eligible_count=1.

        Proves:
        - newly_eligible_count == 1;
        - newly_eligible_ids has exactly 1 entry;
        - the ID matches the application_id in the queue file.
        """
        _clean_fake_jh_data(queue_path)

        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "1"],
            max_jobs=2,
        )

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed", f"Expected completed, got: {final}"

        assert final["newly_eligible_count"] == 1, (
            f"Expected newly_eligible_count 1, got: {final['newly_eligible_count']}"
        )
        newly_eligible_ids = final["newly_eligible_ids"]
        assert len(newly_eligible_ids) == 1, (
            f"Expected 1 newly_eligible_id, got: {newly_eligible_ids}"
        )

        # The persisted ID matches the application_id in the queue file.
        queue_ids = _read_queue_application_ids(queue_path)
        assert len(queue_ids) == 1, f"Expected 1 job in queue, got: {queue_ids}"
        assert newly_eligible_ids[0] == queue_ids[0], (
            f"Newly eligible ID {newly_eligible_ids[0]!r} does not match "
            f"queue application_id {queue_ids[0]!r}"
        )

    def test_idempotent_reimport_newly_eligible_zero(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Re-importing the same queue content produces zero newly eligible jobs.

        Runs parallel orchestration twice with the same queue content (the
        fake producer re-exports the same single job). The second run:

        - newly_eligible_count == 0 (the job is already in a terminal-ish
          state from the first run, so re-import does not re-add it to the
          eligible set);
        - no second pipeline pass (pipeline_run_id is None);
        - application row count unchanged;
        - no new SubmissionResultRow rows.
        """
        _clean_fake_jh_data(queue_path)

        # First orchestration run.
        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "1"],
            max_jobs=2,
        )
        final1 = _wait_for_orchestration_terminal(client, timeout=90)
        assert final1["status"] == "completed", f"Expected completed, got: {final1}"

        # Count jobs and submission results after the first run.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            job_count_after_first = session.execute(
                select(func.count()).select_from(ApplicationJobRow)
            ).scalar_one()
            submission_count_after_first = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        # Wait for the orchestration thread to fully complete before starting
        # a new one.
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        if svc._thread is not None and svc._thread.is_alive():  # type: ignore[attr-defined]
            svc._thread.join(timeout=10)  # type: ignore[attr-defined]

        # Second run with the same queue content (fake producer re-exports).
        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "1"],
            max_jobs=2,
        )
        final2 = _wait_for_orchestration_terminal(client, timeout=90)
        assert final2["status"] == "completed", f"Expected completed, got: {final2}"

        # No newly eligible jobs (the job is already terminal from run 1).
        assert final2["newly_eligible_count"] == 0, (
            f"Expected newly_eligible_count 0 on re-import, got: {final2['newly_eligible_count']}"
        )

        # No second pipeline pass.
        assert final2["pipeline_run_id"] is None, (
            f"Expected no second pipeline pass, got: {final2['pipeline_run_id']}"
        )

        # Application row count unchanged.
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

        # No new submission results.
        assert submission_count_after_second == submission_count_after_first, (
            f"Expected {submission_count_after_first} submissions, "
            f"got {submission_count_after_second}"
        )


class TestParallelProcessingProof:
    """A newly exported job completes its full lifecycle in parallel mode."""

    def test_newly_exported_job_full_lifecycle(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A newly exported job is imported and processed by the second pass.

        Starts parallel orchestration with ``--jobs 1``. Identifies the
        specific application_id from the queue file. Proves:

        - the job was imported (exists in DB);
        - the second pipeline run started (pipeline_run_id is not None and
          differs from pipeline_run_id_initial);
        - the job's status left READY_TO_APPLY/QUEUED and reached a valid
          review terminal state (REVIEW_READY/NEEDS_USER_INPUT/FAILED);
        - both pipeline run IDs are persisted;
        - no job reached SUBMITTED/APPLIED.
        """
        _clean_fake_jh_data(queue_path)

        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "1"],
            max_jobs=2,
        )

        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed", f"Expected completed, got: {final}"

        # Identify the specific application_id from the queue file.
        queue_ids = _read_queue_application_ids(queue_path)
        assert len(queue_ids) == 1, f"Expected 1 job in queue, got: {queue_ids}"
        target_application_id = queue_ids[0]

        # Both pipeline run IDs are persisted.
        assert final["pipeline_run_id_initial"] is not None, (
            "Expected initial pipeline run id to be persisted"
        )
        assert final["pipeline_run_id"] is not None, (
            "Expected second pipeline run id to be persisted"
        )
        assert final["pipeline_run_id"] != final["pipeline_run_id_initial"], (
            "Expected second pipeline run id to differ from initial run id"
        )

        # The job was imported and reached a valid review terminal state.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            job_row = session.get(ApplicationJobRow, target_application_id)
            assert job_row is not None, f"Imported job {target_application_id} not found in DB"
            job_status = str(job_row.status)

            # No job reached SUBMITTED/APPLIED.
            all_jobs = list(session.execute(select(ApplicationJobRow)).scalars().all())
            submission_count = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        assert job_status not in (
            ApplicationStatus.READY_TO_APPLY.value,
            ApplicationStatus.QUEUED.value,
        ), f"Job did not leave READY_TO_APPLY/QUEUED; status is {job_status}"

        assert job_status in (
            ApplicationStatus.REVIEW_READY.value,
            ApplicationStatus.NEEDS_USER_INPUT.value,
            ApplicationStatus.FAILED.value,
        ), f"Job did not reach a valid review terminal state; status is {job_status}"

        for job in all_jobs:
            assert str(job.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job {job.application_id} reached unsafe state {job.status}!"

        assert submission_count == 0, f"Expected 0 submission results, got {submission_count}"
