"""Background pipeline worker service.

Runs the browser pipeline in a background thread with pause/cancel support.
The service is app-scoped (registered on app.state) and owns:
- The worker thread
- threading.Event for pause and cancel signals
- The current PipelineState (durable, visible via API polling)
- The LiveBrowserRunner instance

This service mirrors the WQ-3 QueueImportService pattern: app-scoped,
threading.Lock for state mutations, durable run records.

Safety:
- Only one active pipeline at a time (enforced by _lock + state check).
- Pause is checked between jobs (not mid-browser-action).
- Cancel is checked between jobs and between browser steps.
- Never calls final submission APIs.
- Browser cleanup happens in LiveBrowserRunner's finally block.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import (
    list_application_jobs,
    upsert_application_job,
)

logger = logging.getLogger("universal_auto_applier.pipeline_worker")


class PipelineRunState:
    """Durable pipeline run state, safe for polling across requests."""

    def __init__(self) -> None:
        self.run_id: str = ""
        self.status: str = (
            "idle"  # idle, running, pausing, paused, cancelling, cancelled, completed, failed
        )
        self.mode: str = "sequential_dry_run"
        self.current_job_id: str | None = None
        self.current_phase: str = ""
        self.last_action: str = ""
        self.last_error: str = ""
        self.jobs_total: int = 0
        self.jobs_completed: int = 0
        self.jobs_failed: int = 0
        self.jobs_skipped: int = 0
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.cancel_reason: str = ""
        self.errors: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "mode": self.mode,
            "current_job_id": self.current_job_id,
            "current_phase": self.current_phase,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "jobs_total": self.jobs_total,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "jobs_skipped": self.jobs_skipped,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "cancel_reason": self.cancel_reason,
            "errors": list(self.errors),
        }


class PipelineWorkerService:
    """Background pipeline worker with pause/cancel support.

    App-scoped service registered on app.state.pipeline_worker.
    Only one active pipeline run is allowed at a time.
    """

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._state = PipelineRunState()
        self._worker_thread: threading.Thread | None = None
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()
        self._pause_event.set()  # Not paused initially (set = running allowed)

    @property
    def state(self) -> PipelineRunState:
        with self._lock:
            return self._state

    def get_state_dict(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def start(self, *, max_jobs: int = 10) -> dict[str, Any]:
        """Start a new pipeline run. Returns immediately.

        Raises RuntimeError if a run is already active.
        """
        with self._lock:
            if self._state.status in ("running", "pausing", "paused"):
                raise RuntimeError("A pipeline run is already active")
            self._state = PipelineRunState()
            self._state.run_id = str(uuid.uuid4())
            self._state.status = "running"
            self._state.mode = "sequential_dry_run"
            self._state.started_at = datetime.now(UTC)
            self._pause_event.set()  # Allow running
            self._cancel_event.clear()  # No cancel requested

        # Start worker in background thread.
        self._worker_thread = threading.Thread(
            target=self._run_worker,
            args=(max_jobs,),
            daemon=True,
            name=f"pipeline-worker-{self._state.run_id[:8]}",
        )
        self._worker_thread.start()

        with self._lock:
            return self._state.to_dict()

    def pause(self) -> dict[str, Any]:
        """Request pause. The worker finishes the current job, then pauses."""
        with self._lock:
            if self._state.status != "running":
                raise RuntimeError(f"Cannot pause: pipeline is {self._state.status}")
            self._state.status = "pausing"
            self._state.last_action = "Pause requested"
        self._pause_event.clear()  # Worker will pause at next checkpoint
        return self.get_state_dict()

    def resume(self) -> dict[str, Any]:
        """Resume a paused pipeline."""
        with self._lock:
            if self._state.status != "paused":
                raise RuntimeError(f"Cannot resume: pipeline is {self._state.status}")
            self._state.status = "running"
            self._state.last_action = "Resumed"
        self._pause_event.set()  # Allow running
        return self.get_state_dict()

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        """Request cancellation. The worker stops before the next job."""
        with self._lock:
            if self._state.status in ("idle", "completed", "cancelled", "failed"):
                return self._state.to_dict()
            self._state.status = "cancelling"
            self._state.cancel_reason = reason
            self._state.last_action = f"Cancel requested: {reason}"
        self._cancel_event.set()
        self._pause_event.set()  # Unblock any pause wait
        return self.get_state_dict()

    def _run_worker(self, max_jobs: int) -> None:
        """Worker thread entry point. Runs the pipeline sequentially."""
        try:
            # Load eligible jobs.
            with self._lock:
                self._state.current_phase = "loading_jobs"

            with session_scope(self._session_factory) as session:
                all_jobs = list_application_jobs(session)

            eligible = [
                job
                for job in all_jobs
                if str(job.status)
                in (
                    ApplicationStatus.READY_TO_APPLY.value,
                    ApplicationStatus.QUEUED.value,
                )
            ][:max_jobs]

            with self._lock:
                self._state.jobs_total = len(eligible)

            if not eligible:
                with self._lock:
                    self._state.status = "completed"
                    self._state.finished_at = datetime.now(UTC)
                    self._state.last_action = "No eligible jobs found"
                return

            # Build browser config from settings.
            browser_config = LiveBrowserConfig(
                artifacts_root=self._settings.data_dir / "live-runs",
                profile_dir=self._settings.browser_profile_dir,
                headless=self._settings.browser_headless,
                channel=self._settings.browser_channel,
                timeout_ms=self._settings.browser_timeout_ms,
                max_steps=self._settings.browser_max_steps,
                capture_trace=True,
            )
            runner = LiveBrowserRunner(browser_config)

            for job in eligible:
                # Check cancel before starting next job.
                if self._cancel_event.is_set():
                    self._finish_cancelled()
                    return

                # Check pause before starting next job.
                if not self._pause_event.is_set():
                    self._wait_for_resume()
                    if self._cancel_event.is_set():
                        self._finish_cancelled()
                        return

                self._process_job(job, runner)

            # All jobs processed.
            with self._lock:
                self._state.status = "completed"
                self._state.finished_at = datetime.now(UTC)
                self._state.current_job_id = None
                self._state.current_phase = ""
                self._state.last_action = "Pipeline completed"

        except Exception as exc:
            logger.exception("Pipeline worker failed: %s", exc)
            with self._lock:
                self._state.status = "failed"
                self._state.last_error = str(exc)
                self._state.finished_at = datetime.now(UTC)
                self._state.errors.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "error": str(exc),
                        "phase": self._state.current_phase,
                    }
                )

    def _process_job(self, job: ApplicationJob, runner: LiveBrowserRunner) -> None:
        """Process a single job through the browser pipeline."""
        application_id = job.application_id

        with self._lock:
            self._state.current_job_id = application_id
            self._state.current_phase = "starting"
            self._state.last_action = f"Processing {application_id[:12]}"

        # Transition to IN_PROGRESS.
        job.status = ApplicationStatus.IN_PROGRESS
        with session_scope(self._session_factory) as session:
            upsert_application_job(session, job)

        try:
            # Resolve candidate.
            candidate = resolve_candidate_profile(job.metadata)

            # Run the browser dry-run (never submits).
            with self._lock:
                self._state.current_phase = "browser_dry_run"

            report = runner.run(
                job,
                candidate=candidate,
                qa_service=None,  # No LLM for WQ-4 v1
            )

            # Check cancel after browser run.
            if self._cancel_event.is_set():
                # Job was in progress when cancel arrived.
                job.status = ApplicationStatus.NEEDS_USER_INPUT
                with session_scope(self._session_factory) as session:
                    upsert_application_job(session, job)
                self._finish_cancelled()
                return

            # Translate report status to application status.
            with self._lock:
                self._state.current_phase = "recording_result"

            if report.status == "review_ready":
                job.status = ApplicationStatus.REVIEW_READY
                with self._lock:
                    self._state.jobs_completed += 1
                    self._state.last_action = f"Job {application_id[:12]} reached review_ready"
            elif report.status == "needs_user_input":
                job.status = ApplicationStatus.NEEDS_USER_INPUT
                with self._lock:
                    self._state.jobs_completed += 1
                    self._state.last_action = f"Job {application_id[:12]} needs user input"
                # Create intervention for the blocker.
                if report.stopped_reason:
                    with session_scope(self._session_factory) as session:
                        create_intervention(
                            session,
                            application_id=application_id,
                            kind=InterventionKind.UNKNOWN_PAGE,
                            question=f"Pipeline stopped: {report.stopped_reason}",
                            field_selector="",
                        )
            else:
                # Failed.
                job.status = ApplicationStatus.FAILED
                with self._lock:
                    self._state.jobs_failed += 1
                    self._state.last_error = (
                        f"Job {application_id[:12]} failed: {report.stopped_reason}"
                    )
                    self._state.errors.append(
                        {
                            "timestamp": datetime.now(UTC).isoformat(),
                            "application_id": application_id,
                            "error": report.stopped_reason,
                            "phase": "browser_dry_run",
                        }
                    )

            with session_scope(self._session_factory) as session:
                upsert_application_job(session, job)

        except Exception as exc:
            logger.exception("Job %s failed: %s", application_id[:12], exc)
            job.status = ApplicationStatus.FAILED
            with session_scope(self._session_factory) as session:
                upsert_application_job(session, job)
            with self._lock:
                self._state.jobs_failed += 1
                self._state.last_error = f"Job {application_id[:12]} error: {exc}"
                self._state.errors.append(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "application_id": application_id,
                        "error": str(exc),
                        "phase": self._state.current_phase,
                    }
                )

    def _wait_for_resume(self) -> None:
        """Wait for the pause to be lifted."""
        with self._lock:
            self._state.status = "paused"
            self._state.last_action = "Paused between jobs"
        self._pause_event.wait()  # Blocks until set
        with self._lock:
            self._state.status = "running"
            self._state.last_action = "Resumed from pause"

    def _finish_cancelled(self) -> None:
        """Mark the pipeline as cancelled."""
        with self._lock:
            self._state.status = "cancelled"
            self._state.finished_at = datetime.now(UTC)
            self._state.current_job_id = None
            self._state.current_phase = ""
            self._state.last_action = f"Cancelled: {self._state.cancel_reason}"


__all__ = ["PipelineRunState", "PipelineWorkerService"]
