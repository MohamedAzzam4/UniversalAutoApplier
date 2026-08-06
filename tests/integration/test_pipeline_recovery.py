"""Integration tests for WQ-5 stale pipeline-run recovery.

The recovery runs once in the app lifespan (after migrations, before any
pipeline action). These tests seed a database with a durable run row and a
job, then start the app and observe what startup recovery did:

- a stale run (dead/missing worker pid AND expired/missing heartbeat) is
  recovered to a terminal ``recovered`` state with a durable reason,
- a healthy run (live pid + fresh heartbeat) is NEVER recovered and keeps
  blocking duplicate starts with 409,
- the interrupted ``in_progress`` job becomes ``needs_review`` with exactly
  one idempotent intervention,
- terminal jobs are never downgraded,
- recovery is idempotent across restarts,
- the run's id / counters / error history / current-job context survive,
- no submission table rows are ever created.

Traffic discipline: no network is used; worker processes (when started) run
in fixture mode against local data only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind, Platform
from universal_auto_applier.interventions.store import list_all_interventions
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import SubmissionResultRow
from universal_auto_applier.persistence.pipeline_run_repository import (
    create_pipeline_run,
    update_pipeline_run,
)
from universal_auto_applier.services.pipeline_recovery_service import RECOVERY_QUESTION


def _make_settings(tmp_path: Path, *, heartbeat_timeout_ms: int = 30_000) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq5",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
        pipeline_job_pulse_ms=200,
        pipeline_heartbeat_timeout_ms=heartbeat_timeout_ms,
    )


def _make_job(
    tmp_path: Path,
    external_id: str,
    *,
    status: ApplicationStatus = ApplicationStatus.IN_PROGRESS,
) -> ApplicationJob:
    cv = tmp_path / f"{external_id}-cv.pdf"
    cover = tmp_path / f"{external_id}-cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=Platform.GREENHOUSE.value,
            external_job_id=external_id,
            url=f"https://boards.greenhouse.io/example/jobs/{external_id}",
        ),
        platform=Platform.GREENHOUSE,
        source="test",
        company="Test Corp",
        title="Engineer",
        url=f"https://boards.greenhouse.io/example/jobs/{external_id}",
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=status,
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


def _dead_pid() -> int:
    """Return a pid that is guaranteed to no longer exist."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    return proc.pid


def _old() -> datetime:
    """A heartbeat far older than any configured timeout."""
    return datetime.now(UTC) - timedelta(minutes=10)


def _seed_run(
    factory: Any,
    *,
    run_id: str,
    status: str = "running",
    current_job_id: str | None = None,
    current_phase: str = "",
    worker_pid: int | None = None,
    worker_started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    jobs_total: int = 0,
    jobs_completed: int = 0,
    jobs_failed: int = 0,
    errors_json: list[dict[str, Any]] | None = None,
) -> None:
    with session_scope(factory) as session:
        create_pipeline_run(session, run_id=run_id, status=status, mode="sequential_dry_run")
        update_pipeline_run(
            session,
            run_id,
            current_job_id=current_job_id,
            current_phase=current_phase,
            worker_pid=worker_pid,
            worker_started_at=worker_started_at,
            heartbeat_at=heartbeat_at,
            jobs_total=jobs_total,
            jobs_completed=jobs_completed,
            jobs_failed=jobs_failed,
            errors_json=errors_json or [],
        )


def _seed(
    tmp_path: Path,
    *,
    jobs: list[ApplicationJob] | None = None,
    runs: list[dict[str, Any]] | None = None,
    heartbeat_timeout_ms: int = 30_000,
) -> Settings:
    """Migrate + seed a fresh database (disposing every engine it creates)."""
    settings = _make_settings(tmp_path, heartbeat_timeout_ms=heartbeat_timeout_ms)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = build_engine_url(settings.data_dir / "uaa.sqlite")
    apply_migrations(url)
    engine = make_engine(url)
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            for job in jobs or []:
                upsert_application_job(session, job)
        for run in runs or []:
            _seed_run(factory, **run)
    finally:
        engine.dispose()
    return settings


def _wait_for_terminal(client: TestClient, timeout: float = 30.0) -> dict[str, Any]:
    """Poll pipeline status until a terminal state is reached."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/pipeline/status").json()
        if last["status"] in ("idle", "completed", "cancelled", "failed", "recovered"):
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Pipeline did not reach terminal state in {timeout}s. Last: {last}")


class TestStaleRunRecovery:
    def test_stale_run_recovered_job_to_needs_review_start_allowed(self, tmp_path: Path) -> None:
        """A stale run (dead pid + expired heartbeat) is recovered on startup:
        terminal ``recovered`` with a durable reason, the interrupted
        in_progress job becomes needs_review with one intervention, and a
        fresh start is allowed afterwards (the recovered job is NOT
        auto-retried)."""
        job = _make_job(tmp_path, "rec-1")
        settings = _seed(
            tmp_path,
            jobs=[job],
            runs=[
                {
                    "run_id": "run-stale-1",
                    "current_job_id": job.application_id,
                    "current_phase": "orchestrate",
                    "worker_pid": _dead_pid(),
                    "worker_started_at": _old(),
                    "heartbeat_at": _old(),
                    "jobs_total": 3,
                    "jobs_completed": 1,
                    "jobs_failed": 1,
                    "errors_json": [
                        {
                            "timestamp": _old().isoformat(),
                            "application_id": job.application_id,
                            "error": "boom",
                            "phase": "job",
                        }
                    ],
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "recovered"
            assert state["run_id"] == "run-stale-1"
            # Run context is preserved, not erased.
            assert state["current_job_id"] == job.application_id
            assert state["current_phase"] == "orchestrate"
            assert state["jobs_total"] == 3
            assert state["jobs_completed"] == 1
            assert state["jobs_failed"] == 1
            assert len(state["errors"]) == 1
            assert state["finished_at"] is not None
            assert "interrupted" in state["last_error"]
            assert "review" in state["last_error"].lower()

            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
                interventions = list_all_interventions(session)
            assert updated is not None
            assert str(updated.status) == ApplicationStatus.NEEDS_REVIEW.value
            assert len(interventions) == 1
            assert interventions[0].kind == InterventionKind.RECOVERY
            assert interventions[0].status == "pending"
            assert "will not repeat this job automatically" in interventions[0].question
            assert interventions[0].question == RECOVERY_QUESTION

            # A fresh start works after recovery.
            resp = client.post("/api/pipeline/start", json={"max_jobs": 10})
            assert resp.status_code == 200
            assert resp.json()["run_id"] != "run-stale-1"
            final = _wait_for_terminal(client)
            assert final["status"] == "completed"
            # The recovered job is not in the new run's eligible set.
            assert final["jobs_total"] == 0

            with session_scope(app.state.session_factory) as session:
                updated2 = get_application_job(session, job.application_id)
            assert updated2 is not None
            assert str(updated2.status) == ApplicationStatus.NEEDS_REVIEW.value

    def test_legacy_run_without_liveness_info_recovered(self, tmp_path: Path) -> None:
        """A pre-WQ-5 run row (no pid, no heartbeat) is proven stale."""
        settings = _seed(
            tmp_path,
            runs=[
                {
                    "run_id": "run-legacy-1",
                    "status": "paused",
                    "worker_pid": None,
                    "heartbeat_at": None,
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "recovered"
            assert state["run_id"] == "run-legacy-1"
            assert "missing" in state["last_error"]

    def test_terminal_job_never_downgraded(self, tmp_path: Path) -> None:
        """Recovery may recover the run but must never downgrade a terminal job."""
        job = _make_job(tmp_path, "rec-term", status=ApplicationStatus.APPLIED)
        settings = _seed(
            tmp_path,
            jobs=[job],
            runs=[
                {
                    "run_id": "run-term-1",
                    "current_job_id": job.application_id,
                    "worker_pid": _dead_pid(),
                    "heartbeat_at": _old(),
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "recovered"
            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
                interventions = list_all_interventions(session)
            assert updated is not None
            assert str(updated.status) == ApplicationStatus.APPLIED.value
            assert interventions == []


class TestHealthyRunUntouched:
    def test_healthy_fresh_worker_not_recovered_and_start_blocked(self, tmp_path: Path) -> None:
        """A run owned by a live process with a fresh heartbeat is never
        recovered; duplicate starts still return 409."""
        now = datetime.now(UTC)
        settings = _seed(
            tmp_path,
            runs=[
                {
                    "run_id": "run-healthy-1",
                    "status": "running",
                    "worker_pid": os.getpid(),
                    "worker_started_at": now,
                    "heartbeat_at": now,
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "running"
            assert state["run_id"] == "run-healthy-1"

            resp = client.post("/api/pipeline/start", json={"max_jobs": 1})
            assert resp.status_code == 409
            assert "already active" in resp.json()["detail"].lower()

            # Manual cancel remains the user's way to clear a stale row that
            # still holds a fresh heartbeat.
            resp = client.post("/api/pipeline/cancel")
            assert resp.status_code == 200
            final = _wait_for_terminal(client)
            assert final["status"] == "cancelled"
            assert final["run_id"] == "run-healthy-1"

    def test_fresh_heartbeat_missing_pid_kept(self, tmp_path: Path) -> None:
        """Even without a pid, a fresh heartbeat proves a live worker: kept."""
        now = datetime.now(UTC)
        settings = _seed(
            tmp_path,
            runs=[
                {
                    "run_id": "run-hb-only",
                    "status": "paused",
                    "worker_pid": None,
                    "heartbeat_at": now,
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "paused"
            assert state["run_id"] == "run-hb-only"
            resp = client.post("/api/pipeline/start", json={"max_jobs": 1})
            assert resp.status_code == 409


class TestRecoveryIdempotency:
    def test_second_restart_is_a_noop_with_single_intervention(self, tmp_path: Path) -> None:
        """Recovery is idempotent: a second app start over the same DB finds
        nothing active, keeps the durable state, and never duplicates the
        intervention."""
        job = _make_job(tmp_path, "rec-2")
        settings = _seed(
            tmp_path,
            jobs=[job],
            runs=[
                {
                    "run_id": "run-idem-1",
                    "current_job_id": job.application_id,
                    "worker_pid": _dead_pid(),
                    "heartbeat_at": _old(),
                    "jobs_total": 2,
                    "jobs_completed": 1,
                }
            ],
        )

        app1 = create_app(settings=settings)
        with TestClient(app1) as client1:
            state1 = client1.get("/api/pipeline/status").json()
            assert state1["status"] == "recovered"
            with session_scope(app1.state.session_factory) as session:
                assert len(list_all_interventions(session)) == 1

        app2 = create_app(settings=settings)
        with TestClient(app2) as client2:
            state2 = client2.get("/api/pipeline/status").json()
            assert state2["status"] == "recovered"
            assert state2["run_id"] == "run-idem-1"
            assert state2["jobs_completed"] == 1
            with session_scope(app2.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
                interventions = list_all_interventions(session)
            assert updated is not None
            assert str(updated.status) == ApplicationStatus.NEEDS_REVIEW.value
            assert len(interventions) == 1


class TestRecoverySafety:
    def test_recovery_never_creates_submission_rows(self, tmp_path: Path) -> None:
        """Recovery must never imply or record a submission."""
        job = _make_job(tmp_path, "rec-safe")
        settings = _seed(
            tmp_path,
            jobs=[job],
            runs=[
                {
                    "run_id": "run-safe-1",
                    "current_job_id": job.application_id,
                    "worker_pid": _dead_pid(),
                    "heartbeat_at": _old(),
                }
            ],
        )
        app = create_app(settings=settings)
        with TestClient(app) as client:
            state = client.get("/api/pipeline/status").json()
            assert state["status"] == "recovered"
            with session_scope(app.state.session_factory) as session:
                updated = get_application_job(session, job.application_id)
                n_submission_rows = session.execute(
                    select(func.count()).select_from(SubmissionResultRow)
                ).scalar_one()
            assert updated is not None
            assert str(updated.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            )
            assert n_submission_rows == 0
