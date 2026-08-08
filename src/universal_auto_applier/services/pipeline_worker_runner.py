"""Subprocess entrypoint for the background pipeline worker.

WQ-4 design decision (see ``docs/handoffs/ACTIVE_WORKPACKAGE.md``):

The browser pipeline must never run inside a FastAPI request thread — real
Playwright work in a Python background thread is not stable across Python
3.13/3.14 and leaks resources (guaranteed ``ResourceWarning`` failures under
``filterwarnings = ["error"]``). Instead, the pipeline runs in its own OS
process and is launched by :class:`PipelineWorkerService` through this module.

Contract:

* The server process creates a durable ``pipeline_runs`` row and launches
  ``python -m universal_auto_applier.services.pipeline_worker_runner``.
* This worker reads the run row and the eligible jobs from the shared SQLite
  database, processes them sequentially, and writes every state change back
  through :mod:`pipeline_run_repository` so the API can poll durable progress.
* Pause / cancel are honored at every job boundary by re-reading the run row
  from the database (the control channel). No process-to-process IPC needed.
* ``--job-pulse-ms`` inserts a small wait between jobs during which the
  control row is polled, giving the dashboard a deterministic window in which
  pause/cancel take effect between jobs.
* The worker NEVER performs final submission: fixture mode uses the generic
  orchestrator path (``PipelineOrchestrator.process_job``) and live mode uses
  :class:`LiveBrowserRunner`, both of which stop before any "Submit application"
  control.

Run as::

    python -m universal_auto_applier.services.pipeline_worker_runner \
        --data-dir <dir> --run-id <uuid>
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile
from universal_auto_applier.config import Settings, load_settings
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import (
    list_application_jobs,
    upsert_application_job,
)
from universal_auto_applier.persistence.pipeline_run_repository import (
    get_pipeline_run,
    mark_pipeline_run_terminal,
    update_pipeline_run,
)

logger = logging.getLogger("universal_auto_applier.pipeline_worker_runner")

_PAUSE_POLL_SECONDS = 0.2


def _progress_error(application_id: str | None, error: str, phase: str = "") -> dict[str, Any]:
    """Build one error entry stored in ``pipeline_runs.errors_json``."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "application_id": application_id,
        "error": error,
        "phase": phase,
    }


def _build_live_config(settings: Settings) -> LiveBrowserConfig:
    """Build a :class:`LiveBrowserConfig` from resolved settings."""
    return LiveBrowserConfig(
        artifacts_root=settings.data_dir / "live-runs",
        profile_dir=settings.browser_profile_dir,
        headless=settings.browser_headless,
        channel=settings.browser_channel,
        timeout_ms=settings.browser_timeout_ms,
        max_steps=settings.browser_max_steps,
        capture_trace=True,
    )


class PipelineWorkerRunner:
    """Runs one durable pipeline run in this (sub)process.

    Reads the run row and the eligible jobs from the shared database, then
    processes each job sequentially through either the fixture orchestrator
    path or the live browser path, honoring pause/cancel at every job
    boundary, and persisting every state change.

    Safety guarantees:
    - Live mode never clicks a final submit control (LiveBrowserRunner dry-run).
    - Fixture mode is planning-only dry-run (generic orchestrator path).
    - Cancel is checked before every job; a job already in flight finishes
      safely and the next one is never started.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: Any,
        run_id: str,
        max_jobs: int,
        fixture_file: Path | None = None,
        job_pulse_ms: int,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.run_id = run_id
        self.max_jobs = max(1, max_jobs)
        self.fixture_file = fixture_file
        self.job_pulse_ms = max(0, job_pulse_ms)
        self.mode = "fixture" if fixture_file is not None else "live"
        self._orchestrator: Any | None = None
        self._live_runner: LiveBrowserRunner | None = None

    def run(self) -> int:
        """Execute the run. Returns the process exit code.

        Exit codes:
        0 — run finished (completed or cancelled),
        2 — the run row was missing or not in a runnable state,
        3 — the worker itself failed.
        """
        try:
            return self._run_inner()
        except Exception as exc:  # noqa: BLE001 - last-resort boundary
            logger.exception("pipeline worker runner failed: %s", exc)
            self._mark_failed(str(exc), phase="worker")
            return 3

    def _run_inner(self) -> int:
        with session_scope(self.session_factory) as session:
            run_row = get_pipeline_run(session, self.run_id)
        if run_row is None:
            logger.error("run row %s not found", self.run_id)
            return 2

        # WQ-5 liveness: the worker first heartbeat stamps that it is alive
        # and owns the run, before any control-signal handling.
        self._touch_heartbeat()

        # Control signals can race with worker startup: the server may have
        # cancelled or paused this run before the worker's first read of the
        # run row. Honour those states here instead of refusing to start.
        status = run_row.status
        cancel_reason = run_row.cancel_reason or ""
        if status == "cancelling":
            self._finish_cancelled(cancel_reason)
            return 0
        if status in ("paused", "pausing"):
            if not self._wait_for_resume():
                return 0
        elif status in ("cancelled", "completed", "failed"):
            logger.info("run %s already %s; worker exiting", self.run_id, status)
            return 0
        elif status != "running":
            logger.error("run %s is %r, refusing to start worker", self.run_id, status)
            return 2

        with session_scope(self.session_factory) as session:
            jobs = list_application_jobs(session)
        eligible = [
            job
            for job in jobs
            if str(job.status)
            in (
                ApplicationStatus.READY_TO_APPLY.value,
                ApplicationStatus.QUEUED.value,
            )
        ][: self.max_jobs]

        self._update(
            jobs_total=len(eligible),
            current_phase="loading_jobs",
            last_action=f"Loaded {len(eligible)} eligible jobs ({self.mode} mode)",
        )

        if not eligible:
            self._mark_terminal("completed", "No eligible jobs found")
            return 0

        fixture_html: str | None = None
        if self.fixture_file is not None:
            fixture_html = self.fixture_file.read_text(encoding="utf-8")
        if fixture_html is not None:
            self._orchestrator = self._make_orchestrator()
        else:
            self._live_runner = LiveBrowserRunner(_build_live_config(self.settings))

        pulse_ticks = max(0, self.job_pulse_ms // 100)

        for index, job in enumerate(eligible):
            if index > 0 and not self._at_checkpoint(pulse_ticks):
                return 0
            self._process_job(job, fixture_html)

        self._mark_terminal("completed", "Pipeline completed")
        return 0

    def _make_orchestrator(self) -> Any:
        """Build the fixture-mode orchestrator lazily."""
        from universal_auto_applier.services.pipeline_orchestrator import (
            PipelineOrchestrator,
        )

        return PipelineOrchestrator(
            settings=self.settings,
            session_factory=self.session_factory,
        )

    # ------------------------------------------------------------------
    # Control channel
    # ------------------------------------------------------------------

    def _read_status(self) -> tuple[str, str]:
        """Return (status, cancel_reason) of the run row."""
        with session_scope(self.session_factory) as session:
            row = get_pipeline_run(session, self.run_id)
        if row is None:
            return "completed", ""
        return row.status, row.cancel_reason or ""

    def _at_checkpoint(self, pulse_ticks: int) -> bool:
        """Honor pause/cancel at a job boundary.

        Polls the run row for ``max(1, pulse_ticks)`` times (0.1s apart),
        giving the dashboard a deterministic window to pause or cancel.
        Returns False when the run must stop (cancelled).
        """
        for _ in range(max(1, pulse_ticks)):
            self._touch_heartbeat()
            status, cancel_reason = self._read_status()
            if status == "cancelling":
                self._finish_cancelled(cancel_reason)
                return False
            if status == "pausing":
                return self._wait_for_resume()
            if status in ("cancelled", "completed", "failed"):
                # Terminal state reached without us (e.g. recovery path).
                return False
            if pulse_ticks > 0:
                time.sleep(0.1)
        return True

    def _wait_for_resume(self) -> bool:
        """Mark the run paused and block until resumed.

        Returns True when the run should continue, False when the run was
        cancelled (or otherwise terminated) while paused.
        """
        self._update(status="paused", last_action="Paused between jobs")
        while True:
            self._touch_heartbeat()
            status, cancel_reason = self._read_status()
            if status == "running":
                self._update(status="running", last_action="Resumed from pause")
                return True
            if status == "cancelling":
                self._finish_cancelled(cancel_reason)
                return False
            if status in ("cancelled", "completed", "failed"):
                return False
            time.sleep(_PAUSE_POLL_SECONDS)

    def _finish_cancelled(self, cancel_reason: str) -> None:
        """Mark the run cancelled (terminal)."""
        reason = cancel_reason or "User cancelled"
        self._mark_terminal("cancelled", f"Cancelled: {reason}")

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    def _process_job(self, job: ApplicationJob, fixture_html: str | None) -> None:
        """Process one job and persist counters/errors on the run row."""
        application_id = job.application_id
        self._touch_heartbeat()
        self._update(
            current_job_id=application_id,
            current_phase="starting",
            last_action=f"Processing {application_id[:12]}",
        )

        try:
            if self._orchestrator is not None:
                self._process_job_fixture(job, fixture_html)
            elif self._live_runner is not None:
                self._process_job_live(job)
            else:
                raise RuntimeError("no job processor available")
        except Exception as exc:  # noqa: BLE001 - per-job error boundary
            logger.exception("job %s failed: %s", application_id[:12], exc)
            with session_scope(self.session_factory) as session:
                job.status = ApplicationStatus.FAILED
                upsert_application_job(session, job)
            self._bump(jobs_failed=1)
            self._update(last_error=f"Job {application_id[:12]} error: {exc}")
            self._append_error(_progress_error(application_id, str(exc), "job"))

    def _process_job_fixture(self, job: ApplicationJob, fixture_html: str | None) -> None:
        """Run one job through the generic fixture orchestrator path.

        The orchestrator performs observe -> explore -> extract -> map ->
        fill -> interventions -> review state, and never submits.
        """
        self._update(current_phase="orchestrate", last_action="observe_page")
        assert self._orchestrator is not None
        assert fixture_html is not None
        self._orchestrator.process_job(job, fixture_html)
        if str(job.status) in (
            ApplicationStatus.REVIEW_READY.value,
            ApplicationStatus.NEEDS_USER_INPUT.value,
        ):
            self._bump(jobs_completed=1)
            self._update(
                current_phase="review",
                last_action=f"Job {job.application_id[:12]} reached {job.status}",
            )
        else:
            self._bump(jobs_failed=1)
            self._update(last_error=f"Job {job.application_id[:12]} ended as {job.status}")
            self._append_error(
                _progress_error(
                    job.application_id,
                    f"fixture processing ended as {job.status}",
                    "orchestrate",
                )
            )

    def _process_job_live(self, job: ApplicationJob) -> None:
        """Run one job through the live browser dry-run (never submits)."""
        application_id = job.application_id
        self._update(current_phase="browser_dry_run", last_action="launching_browser")

        candidate = resolve_candidate_profile(job.metadata)
        with session_scope(self.session_factory) as session:
            job.status = ApplicationStatus.IN_PROGRESS
            upsert_application_job(session, job)

        assert self._live_runner is not None
        report = self._live_runner.run(job, candidate=candidate, qa_service=None)

        if report.status == "review_ready":
            self._update(
                current_phase="recording_result",
                last_action=f"Job {application_id[:12]} reached review_ready",
            )
            self._bump(jobs_completed=1)
            self._set_job_status(job, ApplicationStatus.REVIEW_READY)
        elif report.status == "needs_user_input":
            self._update(
                current_phase="recording_result",
                last_action=f"Job {application_id[:12]} needs user input",
            )
            self._bump(jobs_completed=1)
            self._set_job_status(job, ApplicationStatus.NEEDS_USER_INPUT)
            if report.stopped_reason:
                with session_scope(self.session_factory) as session:
                    create_intervention(
                        session,
                        application_id=application_id,
                        kind=InterventionKind.UNKNOWN_PAGE,
                        question=f"Pipeline stopped: {report.stopped_reason}",
                        field_selector="",
                    )
        else:
            self._update(last_error=f"Job {application_id[:12]} failed: {report.stopped_reason}")
            self._bump(jobs_failed=1)
            self._set_job_status(job, ApplicationStatus.FAILED)
            self._append_error(
                _progress_error(application_id, report.stopped_reason, "browser_dry_run")
            )

    def _set_job_status(self, job: ApplicationJob, status: ApplicationStatus) -> None:
        """Persist a job status transition."""
        job.status = status
        with session_scope(self.session_factory) as session:
            upsert_application_job(session, job)

    # ------------------------------------------------------------------
    # Run row persistence
    # ------------------------------------------------------------------

    def _update(self, **changes: Any) -> None:
        """Apply changes to the run row."""
        with session_scope(self.session_factory) as session:
            update_pipeline_run(session, self.run_id, **changes)

    def _touch_heartbeat(self) -> None:
        """Stamp the run's heartbeat so recovery never mistakes us for stale.

        Called continuously while the worker is active — including while
        paused/waiting — so a live worker always owns its run.
        """
        self._update(heartbeat_at=datetime.now(UTC))

    def _bump(self, *, jobs_completed: int = 0, jobs_failed: int = 0) -> None:
        """Increment run counters atomically."""
        with session_scope(self.session_factory) as session:
            row = get_pipeline_run(session, self.run_id)
            if row is None:
                return
            update_pipeline_run(
                session,
                self.run_id,
                jobs_completed=row.jobs_completed + jobs_completed,
                jobs_failed=row.jobs_failed + jobs_failed,
            )

    def _append_error(self, entry: dict[str, Any]) -> None:
        """Append one entry to the run's errors list."""
        with session_scope(self.session_factory) as session:
            row = get_pipeline_run(session, self.run_id)
            if row is None:
                return
            errors = list(row.errors_json or [])
            errors.append(entry)
            update_pipeline_run(session, self.run_id, errors_json=errors)

    def _mark_terminal(self, status: str, last_action: str) -> None:
        """Mark the run terminal (completed / cancelled / failed)."""
        with session_scope(self.session_factory) as session:
            mark_pipeline_run_terminal(
                session,
                self.run_id,
                status=status,
                last_action=last_action,
            )

    def _mark_failed(self, error: str, phase: str) -> None:
        """Mark the run failed with an error entry (best-effort)."""
        try:
            self._append_error(_progress_error(None, error, phase))
            with session_scope(self.session_factory) as session:
                mark_pipeline_run_terminal(
                    session,
                    self.run_id,
                    status="failed",
                    last_action=f"Worker failed: {error}",
                    last_error=error,
                )
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.exception("failed to persist worker failure state")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse worker CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="universal_auto_applier.pipeline_worker_runner",
        description="Run one durable pipeline run in a subprocess.",
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-jobs", type=int, default=10)
    parser.add_argument("--fixture-file", type=Path, default=None)
    parser.add_argument("--job-pulse-ms", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by ``python -m ...``."""
    args = _parse_args(argv)
    settings = load_settings().model_copy(update={"data_dir": args.data_dir})
    engine = make_engine(build_engine_url(args.data_dir / "uaa.sqlite"))
    factory = make_session_factory(engine)
    try:
        runner = PipelineWorkerRunner(
            settings=settings,
            session_factory=factory,
            run_id=args.run_id,
            max_jobs=args.max_jobs,
            fixture_file=args.fixture_file,
            job_pulse_ms=args.job_pulse_ms,
        )
        return runner.run()
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
