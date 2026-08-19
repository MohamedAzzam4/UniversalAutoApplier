"""Hermetic tests for the WQ-7C synthetic orchestration opt-in.

These tests prove the synthetic orchestration contract:

- Synthetic mode is an explicit opt-in that is incompatible with real
  submission mode and with parallel mode (rejected at start).
- Synthetic mode requires an explicit, already-existing queue path
  (a pre-produced synthetic queue).
- The production JobHunter workflow is never re-run: ``jobhunter_pid`` stays
  None and no JobHunter subprocess is launched.
- The queue import runs with ``synthetic_mutation=True`` so every imported
  row is identity-checked and stamped with WQ-7C synthetic markers; a
  non-synthetic row is refused (never stamped).
- ONLY the newly imported application IDs are targeted by the UAA pipeline
  (a pre-existing eligible job is never targeted).
- No job ever reaches SUBMITTED/APPLIED and no SubmissionResultRow is created.

All tests are hermetic: a fixed pre-produced queue file on disk, a recording
fake pipeline worker, and fixture PDFs. No real ATS, no public web, no real
submission.
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
from universal_auto_applier.services.orchestration_service import (
    OrchestrationConfigurationError,
)

SYNTHETIC_FULL_NAME = "Test Candidate"
SYNTHETIC_EMAIL = "test.candidate@example.com"


class _RecordingPipelineWorker:
    """Fake pipeline worker that records every start() call and completes.

    The orchestration service polls ``get_state_dict()`` until the status is
    terminal; returning ``completed`` immediately lets
    ``_wait_for_pipeline`` break on the first poll.
    """

    def __init__(self) -> None:
        self.start_calls: list[dict[str, Any]] = []

    def start(
        self,
        *,
        max_jobs: int = 10,
        fixture_html: str | None = None,
        target_application_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        call = {
            "max_jobs": max_jobs,
            "fixture_html": fixture_html,
            "target_application_ids": list(target_application_ids or []),
        }
        self.start_calls.append(call)
        return {"run_id": f"fake-pipeline-{len(self.start_calls)}", "status": "completed"}

    def get_state_dict(self) -> dict[str, Any]:
        return {"status": "completed"}

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        self.start_calls.append({"cancelled": True})
        return {"status": "cancelled"}

    def shutdown(self) -> None:
        return None


def _make_settings(tmp_path: Path, *, queue_path: Path | None = None, **overrides: Any) -> Settings:
    """Build settings for synthetic orchestration tests (no JobHunter repo)."""
    kwargs: dict[str, Any] = dict(
        host="127.0.0.1",
        port=8401,
        data_dir=tmp_path / "uaa_wq7c_synthetic",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_timeout_ms=5000,
        browser_max_steps=3,
        pipeline_job_pulse_ms=200,
        orchestration_mode="sequential",
        jobhunter_timeout_seconds=0,
    )
    if queue_path is not None:
        kwargs["queue_path"] = queue_path
    kwargs.update(overrides)
    return Settings(**kwargs)


def _seed_eligible_job(session_factory: Any, tmp_path: Path) -> str:
    """Seed a NON-synthetic eligible job already in the DB.

    Returns its application_id. This job must NOT be targeted by a synthetic
    orchestration run (only newly imported IDs are targeted).
    """
    from universal_auto_applier.persistence.job_repository import upsert_application_job

    cv = tmp_path / "existing-cv.pdf"
    cover = tmp_path / "existing-cover.pdf"
    cv.write_bytes(b"%PDF existing")
    cover.write_bytes(b"%PDF existing")
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="existing-nonsynthetic", url=""
    )
    job = ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="pre_seeded",
        company="Existing Corp",
        title="Existing Engineer",
        url="https://boards.greenhouse.io/example/jobs/existing-nonsynthetic",
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="existing-nonsynthetic",
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
    with session_scope(session_factory) as session:
        upsert_application_job(session, job)
    return application_id


def _write_synthetic_queue(queue_path: Path, external_id: str = "wq7c-synthetic-1") -> str:
    """Write a pre-produced queue with ONE synthetic-identity job.

    Returns the queue application_id.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    platform = "greenhouse"
    url = f"https://boards.greenhouse.io/synthetic/jobs/{external_id}"
    application_id = hashlib.sha256(f"{platform}:{external_id}".encode()).hexdigest()
    cv_path = queue_path.parent / f"{external_id}-cv.pdf"
    cover_path = queue_path.parent / f"{external_id}-cover.pdf"
    cv_path.write_bytes(b"%PDF synthetic cv")
    cover_path.write_bytes(b"%PDF synthetic cover")
    job = {
        "application_id": application_id,
        "platform": platform,
        "external_job_id": external_id,
        "source": "synthetic_pipeline",
        "company": "Synthetic Corp",
        "title": "Synthetic AI Engineer",
        "url": url,
        "verdict": "apply",
        "status": "ready_to_apply",
        "score": 4.5,
        "cv_pdf": str(cv_path),
        "cover_letter_pdf": str(cover_path),
        "metadata": {
            "candidate_profile": {
                "first_name": "Test",
                "last_name": "Candidate",
                "full_name": SYNTHETIC_FULL_NAME,
                "email": SYNTHETIC_EMAIL,
                "phone": "+1 555 0199",
                "city": "Test City",
                "country": "Syntheticland",
                "linkedin": "",
                "website": "https://example.com/test-candidate",
                "requires_sponsorship": False,
                "years_of_experience": 5,
                "current_role": "Synthetic Test Engineer",
            },
        },
    }
    queue_path.write_text(json.dumps(job, separators=(",", ":")) + "\n", encoding="utf-8")
    return application_id


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    return tmp_path / "synthetic_workdir" / "data" / "application_queue.jsonl"


@pytest.fixture
def app_settings(tmp_path: Path, queue_path: Path) -> Settings:
    settings = _make_settings(tmp_path, queue_path=queue_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    return settings


@pytest.fixture
def client(app_settings: Settings) -> Any:
    app = create_app(settings=app_settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        test_client.app.state.recording_worker = _RecordingPipelineWorker()  # type: ignore[attr-defined]
        svc = test_client.app.state.orchestration_service  # type: ignore[union-attr]
        svc._pipeline_worker = test_client.app.state.recording_worker  # type: ignore[attr-defined]
        yield test_client
        orch = getattr(app.state, "orchestration_service", None)
        if orch is not None and hasattr(orch, "shutdown"):
            orch.shutdown()
        worker = getattr(app.state, "recording_worker", None)
        if worker is not None and hasattr(worker, "shutdown"):
            worker.shutdown()


def _start_synthetic(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    svc = client.app.state.orchestration_service  # type: ignore[union-attr]
    return svc.start(synthetic_orchestration=True, max_jobs=5, **kwargs)


def _wait_for_terminal(client: TestClient, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get("/api/orchestration/status").json()
        if last["status"] in ("idle", "completed", "failed", "cancelled"):
            return last
        time.sleep(0.2)
    raise RuntimeError(f"Orchestration did not reach terminal state in {timeout}s. Last: {last}")


class TestSyntheticValidation:
    def test_synthetic_parallel_rejected(self, client: TestClient, queue_path: Path) -> None:
        _write_synthetic_queue(queue_path)
        with pytest.raises(OrchestrationConfigurationError, match="sequential"):
            _start_synthetic(client, mode="parallel")

    def test_synthetic_rejects_real_submission(self, tmp_path: Path, queue_path: Path) -> None:
        settings = _make_settings(tmp_path, queue_path=queue_path, enable_real_submission=True)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        app = create_app(settings=settings)
        with TestClient(app) as test_client:
            Base.metadata.create_all(app.state.engine)
            svc = test_client.app.state.orchestration_service  # type: ignore[union-attr]
            _write_synthetic_queue(queue_path)
            with pytest.raises(OrchestrationConfigurationError, match="real submission"):
                svc.start(synthetic_orchestration=True)
            orch = getattr(app.state, "orchestration_service", None)
            if orch is not None:
                orch.shutdown()

    def test_synthetic_requires_queue_path(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, queue_path=None)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        app = create_app(settings=settings)
        with TestClient(app) as test_client:
            Base.metadata.create_all(app.state.engine)
            svc = test_client.app.state.orchestration_service  # type: ignore[union-attr]
            with pytest.raises(OrchestrationConfigurationError, match="queue path"):
                svc.start(synthetic_orchestration=True)
            orch = getattr(app.state, "orchestration_service", None)
            if orch is not None:
                orch.shutdown()

    def test_synthetic_requires_existing_queue(self, client: TestClient) -> None:
        # queue_path does not exist (fixture never wrote it).
        with pytest.raises(OrchestrationConfigurationError, match="pre-produced queue"):
            _start_synthetic(client)


class TestSyntheticHappyPath:
    def test_imports_markers_and_targets_only_new_ids(
        self,
        client: TestClient,
        queue_path: Path,
        tmp_path: Path,
    ) -> None:
        # A non-synthetic eligible job already exists in the DB.
        pre_seeded_id = _seed_eligible_job(client.app.state.session_factory, tmp_path)
        queue_id = _write_synthetic_queue(queue_path)

        state = _start_synthetic(client)
        assert state["synthetic_orchestration"] is True

        final = _wait_for_terminal(client, timeout=60)
        assert final["status"] == "completed", f"Unexpected status: {final}"

        # Contract: synthetic flag persisted, JobHunter never re-run.
        assert final["synthetic_orchestration"] is True
        assert final["jobhunter_pid"] is None
        assert final["jobhunter_exit_code"] == 0
        assert "jobhunter production workflow not re-run" in final["jobhunter_stdout"].lower()

        # Queue published + imported exactly once.
        assert final["queue_published"] is True
        assert final["queue_import_state"] == "success"
        assert final["queue_imported"] == 1

        # Only the newly imported ID is targeted.
        assert final["newly_eligible_count"] == 1
        assert final["newly_eligible_ids"] == [queue_id]
        assert final["targeted_ids"] == [queue_id]
        assert pre_seeded_id not in final["targeted_ids"]

        # The pipeline worker observed the exact targeting.
        worker = client.app.state.recording_worker  # type: ignore[union-attr]
        assert len(worker.start_calls) == 1
        assert worker.start_calls[0]["target_application_ids"] == [queue_id]

        # Imported job carries the WQ-7C synthetic markers.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            row = session.get(ApplicationJobRow, queue_id)
            assert row is not None
            profile = (row.metadata_json or {}).get("candidate_profile", {})
            assert profile.get("synthetic_test") is True
            assert profile.get("wq7_synthetic") is True

            # The pre-seeded job was never touched by synthetic import.
            existing = session.get(ApplicationJobRow, pre_seeded_id)
            assert existing is not None
            existing_profile = (existing.metadata_json or {}).get("candidate_profile", {})
            assert existing_profile.get("synthetic_test") is None

            jobs = list(session.execute(select(ApplicationJobRow)).scalars().all())
            submission_count = session.execute(
                select(func.count()).select_from(SubmissionResultRow)
            ).scalar_one()

        assert submission_count == 0, f"Expected 0 submission results, got {submission_count}"
        for job in jobs:
            assert str(job.status) not in (
                ApplicationStatus.SUBMITTED.value,
                ApplicationStatus.APPLIED.value,
            ), f"Job {job.application_id} reached unsafe state {job.status}!"

    def test_synthetic_non_synthetic_row_refused(
        self, client: TestClient, queue_path: Path
    ) -> None:
        """A queue row whose snapshot is NOT the synthetic identity is refused."""
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        external_id = "real-candidate-row"
        cv = queue_path.parent / f"{external_id}-cv.pdf"
        cover = queue_path.parent / f"{external_id}-cover.pdf"
        cv.write_bytes(b"%PDF cv")
        cover.write_bytes(b"%PDF cover")
        app_id = hashlib.sha256(f"lever:{external_id}".encode()).hexdigest()
        job = {
            "application_id": app_id,
            "platform": "lever",
            "external_job_id": external_id,
            "source": "real_pipeline",
            "company": "Real Corp",
            "title": "Engineer",
            "url": "https://jobs.lever.co/real/123",
            "verdict": "apply",
            "status": "ready_to_apply",
            "score": 4.0,
            "cv_pdf": str(cv),
            "cover_letter_pdf": str(cover),
            "metadata": {
                "candidate_profile": {
                    "first_name": "Real",
                    "last_name": "Person",
                    "full_name": "Real Person",
                    "email": "real.person@corp.example",
                    "requires_sponsorship": False,
                },
            },
        }
        queue_path.write_text(json.dumps(job, separators=(",", ":")) + "\n", encoding="utf-8")

        _start_synthetic(client)
        final = _wait_for_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["queue_imported"] == 0
        assert final["newly_eligible_count"] == 0
        # The row was refused (never stamped) and skipped.
        with session_scope(client.app.state.session_factory) as session:  # type: ignore[union-attr]
            row = session.get(ApplicationJobRow, app_id)
            assert row is None, "Non-synthetic row must not be imported"

    def test_synthetic_no_new_jobs_no_pipeline(
        self, client: TestClient, app_settings: Settings, queue_path: Path
    ) -> None:
        # Import the synthetic job once (direct import), then orchestrate the
        # same pre-produced queue: nothing is newly eligible, no pipeline runs.
        from universal_auto_applier.services.queue_import_service import QueueImportService

        _write_synthetic_queue(queue_path)
        importer = QueueImportService(app_settings, client.app.state.session_factory)  # type: ignore[union-attr]
        first = importer.run(path=queue_path, trigger="pre-seed", synthetic_mutation=True)
        assert first.imported == 1

        # Reset the recording worker so we can assert no pipeline pass occurs.
        client.app.state.recording_worker.start_calls.clear()  # type: ignore[union-attr]

        _start_synthetic(client)
        final = _wait_for_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["newly_eligible_count"] == 0
        assert final["pipeline_run_id"] is None
        worker = client.app.state.recording_worker  # type: ignore[union-attr]
        assert worker.start_calls == [], "Pipeline must not run with zero new IDs"


class TestSyntheticApiRoute:
    def test_start_endpoint_accepts_synthetic_flag(
        self, client: TestClient, queue_path: Path
    ) -> None:
        _write_synthetic_queue(queue_path)
        resp = client.post(
            "/api/orchestration/start",
            json={
                "mode": "sequential",
                "synthetic_orchestration": True,
                "max_jobs": 3,
            },
        )
        assert resp.status_code == 200, f"Unexpected response: {resp.text}"
        body = resp.json()
        assert body["synthetic_orchestration"] is True

        final = _wait_for_terminal(client, timeout=60)
        assert final["status"] == "completed"
        assert final["synthetic_orchestration"] is True
        assert final["queue_imported"] == 1

    def test_start_endpoint_rejects_synthetic_parallel(
        self, client: TestClient, queue_path: Path
    ) -> None:
        _write_synthetic_queue(queue_path)
        resp = client.post(
            "/api/orchestration/start",
            json={
                "mode": "parallel",
                "synthetic_orchestration": True,
                "max_jobs": 3,
            },
        )
        assert resp.status_code == 400, f"Unexpected response: {resp.text}"
        assert "sequential" in resp.json()["detail"]
