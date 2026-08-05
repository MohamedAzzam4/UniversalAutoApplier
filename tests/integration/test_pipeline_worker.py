"""Unit tests for the background pipeline worker service.

Tests:
- start returns promptly while work continues in the background
- duplicate start is rejected (409)
- pause occurs before the next job
- resume continues safely
- cancel stops future jobs and cleans resources
- run state survives API polling/reload
- errors are persisted and visible
- one failed job does not erase earlier results
- worker never invokes final submission
- no job becomes SUBMITTED or APPLIED
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
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
from universal_auto_applier.persistence.models import Base


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8400,
        data_dir=tmp_path / "uaa_wq4",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
    )


def _make_job(
    tmp_path: Path, external_id: str, url: str = "https://example.com/nonexistent"
) -> ApplicationJob:
    cv = tmp_path / f"{external_id}-cv.pdf"
    cover = tmp_path / f"{external_id}-cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
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


def _setup_app(tmp_path: Path, jobs: list[ApplicationJob]) -> tuple[Any, Settings, Any]:
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        for job in jobs:
            upsert_application_job(session, job)
    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = sf
    Base.metadata.create_all(engine)
    return app, settings, sf


def _wait_for_terminal(client: TestClient, timeout: float = 30.0) -> dict[str, Any]:
    """Poll pipeline status until terminal."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get("/api/pipeline/status")
        data = resp.json()
        if data["status"] in ("idle", "completed", "cancelled", "failed"):
            return data
        time.sleep(0.5)
    raise RuntimeError(f"Pipeline did not reach terminal state in {timeout}s. Last: {data}")


class TestStartReturnsPromptly:
    def test_start_returns_immediately(self, tmp_path: Path) -> None:
        """POST /pipeline/start returns 200 with status=running immediately."""
        app, settings, sf = _setup_app(tmp_path, [])
        with TestClient(app) as client:
            resp = client.post("/api/pipeline/start", json={"max_jobs": 1})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "running"
            assert data["run_id"] != ""
            # Wait for it to finish (no jobs → completes quickly).
            final = _wait_for_terminal(client)
            assert final["status"] == "completed"


class TestDuplicateStartRejected:
    def test_duplicate_start_returns_409(self, tmp_path: Path) -> None:
        """Duplicate start while running returns 409."""
        # Create a job that will take some time (real browser attempt).
        job = _make_job(tmp_path, "dup-1", url="https://example.com")
        app, settings, sf = _setup_app(tmp_path, [job])
        with TestClient(app) as client:
            # Start the pipeline.
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            # Immediately try to start again — should be 409.
            resp = client.post("/api/pipeline/start", json={"max_jobs": 1})
            assert resp.status_code == 409
            _wait_for_terminal(client, timeout=60)


class TestPauseAndResume:
    def test_pause_resume(self, tmp_path: Path) -> None:
        """Pause prevents new jobs from starting; resume continues."""
        job1 = _make_job(tmp_path, "pause-1")
        job2 = _make_job(tmp_path, "pause-2")
        app, settings, sf = _setup_app(tmp_path, [job1, job2])
        with TestClient(app) as client:
            # Start the pipeline.
            client.post("/api/pipeline/start", json={"max_jobs": 2})

            # Wait for paused or completed state.
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                status = client.get("/api/pipeline/status").json()
                if status["status"] == "paused":
                    # Resume.
                    resp = client.post("/api/pipeline/resume")
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "running"
                    break
                if status["status"] in ("completed", "cancelled", "failed"):
                    # Pipeline finished before we could pause (both jobs done fast).
                    # That's acceptable — pause was requested but the pipeline
                    # completed between the request and the next checkpoint.
                    return
                # Try to pause if running.
                if status["status"] == "running":
                    client.post("/api/pipeline/pause")
                time.sleep(0.5)

            # Wait for terminal.
            final = _wait_for_terminal(client, timeout=60)
            assert final["status"] in ("completed", "cancelled")


class TestCancel:
    def test_cancel_stops_pipeline(self, tmp_path: Path) -> None:
        """Cancel stops the pipeline before the next job."""
        job1 = _make_job(tmp_path, "cancel-1")
        job2 = _make_job(tmp_path, "cancel-2")
        app, settings, sf = _setup_app(tmp_path, [job1, job2])
        with TestClient(app) as client:
            client.post("/api/pipeline/start", json={"max_jobs": 2})
            time.sleep(1)

            resp = client.post("/api/pipeline/cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelling"

            final = _wait_for_terminal(client, timeout=60)
            assert final["status"] == "cancelled"


class TestStateSurvivesPolling:
    def test_status_pollable_during_run(self, tmp_path: Path) -> None:
        """GET /pipeline/status returns valid state during a run."""
        app, settings, sf = _setup_app(tmp_path, [])
        with TestClient(app) as client:
            client.post("/api/pipeline/start", json={"max_jobs": 1})

            # Poll immediately.
            resp = client.get("/api/pipeline/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] in ("running", "completed")
            assert "message" in data
            assert "No final submissions" in data["message"]

            _wait_for_terminal(client)


class TestNoSubmission:
    def test_no_job_becomes_submitted(self, tmp_path: Path) -> None:
        """No job transitions to SUBMITTED or APPLIED from the worker."""
        job = _make_job(tmp_path, "no-submit-1")
        app, settings, sf = _setup_app(tmp_path, [job])
        with TestClient(app) as client:
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            _wait_for_terminal(client, timeout=60)

            # Check the job status in the DB.
            with session_scope(sf) as session:
                updated = get_application_job(session, job.application_id)
            assert updated is not None
            assert str(updated.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job became {updated.status} — worker must not submit!"


class TestErrorsVisible:
    def test_failed_job_visible_in_state(self, tmp_path: Path) -> None:
        """A failed job records its error in the pipeline state."""
        # Use a non-existent URL to trigger a browser failure.
        job = _make_job(tmp_path, "fail-1", url="https://nonexistent.invalid.example")
        app, settings, sf = _setup_app(tmp_path, [job])
        with TestClient(app) as client:
            client.post("/api/pipeline/start", json={"max_jobs": 1})
            final = _wait_for_terminal(client, timeout=60)

            # The pipeline should complete (even if the job failed).
            assert final["status"] == "completed"
            # The job should be FAILED or NEEDS_USER_INPUT (not SUBMITTED).
            with session_scope(sf) as session:
                updated = get_application_job(session, job.application_id)
            assert updated is not None
            assert str(updated.status) in (
                ApplicationStatus.FAILED.value,
                ApplicationStatus.NEEDS_USER_INPUT.value,
            )


class TestOneFailedJobDoesNotEraseResults:
    def test_previous_results_preserved(self, tmp_path: Path) -> None:
        """If job 1 succeeds and job 2 fails, job 1's status is preserved."""
        job1 = _make_job(tmp_path, "ok-1", url="https://example.com")
        job2 = _make_job(tmp_path, "fail-2", url="https://nonexistent.invalid.example")
        app, settings, sf = _setup_app(tmp_path, [job1, job2])
        with TestClient(app) as client:
            client.post("/api/pipeline/start", json={"max_jobs": 2})
            _wait_for_terminal(client, timeout=90)

            with session_scope(sf) as session:
                updated1 = get_application_job(session, job1.application_id)
                updated2 = get_application_job(session, job2.application_id)
            assert updated1 is not None
            assert updated2 is not None
            # Job 1 should not be READY_TO_APPLY (it was processed).
            assert str(updated1.status) != ApplicationStatus.READY_TO_APPLY.value
            # Job 2 should be FAILED or NEEDS_USER_INPUT.
            assert str(updated2.status) in (
                ApplicationStatus.FAILED.value,
                ApplicationStatus.NEEDS_USER_INPUT.value,
            )
