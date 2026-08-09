"""Cross-repository orchestration service (WQ-6).

Coordinates JobHunter export -> UAA queue import -> UAA pipeline in either
sequential or parallel mode. The service owns the durable
:class:`OrchestrationRunRow`, the :class:`JobHunterRunner` subprocess, and
the link to the existing :class:`PipelineWorkerService`.

Safety:
- Only one active orchestration run may exist (in-process lock + DB status).
- Duplicate start returns HTTP 409.
- Cancellation requests UAA pipeline cancellation safely and terminates only
  the owned JobHunter child process (graceful first, forced only after grace).
- Never kills a process based solely on an unverified stale PID.
- Never performs final submission. The UAA pipeline is dry-run/review-only.
- Never imports JobHunter Python modules. The boundary is process-level.
- Never places tokens, API keys, CV data, or candidate data in logs or args.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.orchestration_run_repository import (
    ACTIVE_STATUSES,
    create_orchestration_run,
    get_active_orchestration_run,
    get_latest_orchestration_run,
    mark_orchestration_run_terminal,
    orchestration_run_to_dict,
    update_orchestration_run,
)
from universal_auto_applier.services.jobhunter_runner import (
    JobHunterRunner,
)

logger = logging.getLogger("universal_auto_applier.orchestration_service")


class OrchestrationConfigurationError(RuntimeError):
    """Raised when orchestration configuration is incomplete or invalid."""


class OrchestrationConcurrentError(RuntimeError):
    """Raised when an orchestration run is already active."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrchestrationService:
    """Coordinates JobHunter + UAA import + UAA pipeline.

    One instance is shared per running app (stored on ``app.state``) so its
    in-process lock serializes concurrent orchestration start attempts.
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: Any,
        pipeline_worker: Any,
        queue_import_service: Any,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._pipeline_worker = pipeline_worker
        self._queue_import_service = queue_import_service
        self._lock = threading.Lock()
        self._runner: JobHunterRunner | None = None
        self._thread: threading.Thread | None = None
        self._cancel_requested = threading.Event()
        self._jobhunter_extra_args: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        mode: str | None = None,
        fixture_html: str | None = None,
        max_jobs: int = 10,
        jobhunter_extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start a new orchestration run in a background thread.

        Returns immediately with the durable run state. The run executes in
        a daemon thread; poll ``GET /api/orchestration/status`` for progress.

        Raises:
            OrchestrationConcurrentError: if a run is already active.
            OrchestrationConfigurationError: if configuration is invalid.
        """
        resolved_mode = mode or self._settings.orchestration_mode
        if resolved_mode not in ("sequential", "parallel"):
            raise OrchestrationConfigurationError(
                f"Invalid orchestration mode: {resolved_mode!r} (expected 'sequential' or 'parallel')"
            )

        with self._lock:
            # Check DB for an active run.
            with session_scope(self._session_factory) as session:
                active = get_active_orchestration_run(session)
            if active is not None:
                raise OrchestrationConcurrentError(
                    f"An orchestration run is already active: {active.run_id} ({active.status})"
                )

            # Validate configuration before creating the run row.
            self._validate_config(resolved_mode)

            run_id = str(uuid.uuid4())
            with session_scope(self._session_factory) as session:
                create_orchestration_run(session, run_id=run_id, mode=resolved_mode)

            self._cancel_requested.clear()
            self._jobhunter_extra_args = jobhunter_extra_args or []
            # Wait for any previous orchestration thread to fully complete
            # before starting a new one. This prevents the previous runner's
            # Popen handle from being GC'd while its child is still running.
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=30)
            # Ensure the previous runner's subprocess has been waited on.
            if self._runner is not None and self._runner.is_alive:
                try:
                    self._runner.cancel()
                except Exception:  # noqa: BLE001
                    logger.warning("[orchestration] error cleaning up previous runner")
            # Clean up any previous runner handle.
            self._runner = None
            self._thread = threading.Thread(
                target=self._run,
                args=(run_id, resolved_mode, fixture_html, max_jobs),
                daemon=True,
                name=f"orchestration-{run_id[:8]}",
            )
            self._thread.start()

            with session_scope(self._session_factory) as session:
                row = get_latest_orchestration_run(session)
            return orchestration_run_to_dict(row)

    def status(self) -> dict[str, Any]:
        """Return the latest orchestration run state (durable, pollable)."""
        with session_scope(self._session_factory) as session:
            row = get_latest_orchestration_run(session)
        return orchestration_run_to_dict(row)

    def shutdown(self) -> None:
        """Terminate the owned JobHunter subprocess (best-effort) on app shutdown.

        Called on app shutdown. The run row is left in its current state —
        startup recovery handles orphaned runs. Never kills based on a stale
        PID — uses the owned :class:`Popen` handle.
        """
        runner = self._runner
        if runner is not None and runner.is_alive:
            try:
                runner.cancel()
            except Exception:  # noqa: BLE001 - shutdown must not crash
                logger.warning("[orchestration] error cancelling JobHunter during shutdown")
        elif runner is not None:
            # The runner already exited; ensure its Popen returncode is set
            # to prevent ResourceWarning from Popen.__del__.
            try:
                runner._ensure_reaped()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        # Wait for the orchestration thread to finish (bounded).
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        """Request cancellation of the active orchestration run.

        - Requests UAA pipeline cancellation safely (if a pipeline is running).
        - Terminates only the owned JobHunter child process (graceful first).
        - Never kills a process based solely on an unverified stale PID.
        - Persists the final cancellation outcome.
        """
        with self._lock:
            with session_scope(self._session_factory) as session:
                active = get_active_orchestration_run(session)
            if active is None:
                return self.status()

            self._cancel_requested.set()
            run_id = active.run_id

            # Update the run row to reflect cancellation is in progress.
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    status="cancelling",
                    current_phase="cancelling",
                    last_action=f"Cancel requested: {reason}",
                    cancel_reason=reason,
                )

            # Cancel the UAA pipeline safely (if it is running).
            try:
                self._pipeline_worker.cancel(reason=reason)
            except Exception:  # noqa: BLE001 - cancellation must not crash
                logger.warning("[orchestration] pipeline cancel failed during orchestration cancel")

            # Terminate the owned JobHunter subprocess (if it is running).
            exit_code: int | None = None
            if self._runner is not None and self._runner.is_alive:
                exit_code = self._runner.cancel()
                result = self._runner.collect_result(exit_code, cancelled=True)
                with session_scope(self._session_factory) as session:
                    update_orchestration_run(
                        session,
                        run_id,
                        jobhunter_exit_code=exit_code,
                        jobhunter_finished_at=_utcnow(),
                        jobhunter_stdout=result.stdout,
                        jobhunter_stderr=result.stderr,
                    )

            # Wait for the orchestration thread to finish (bounded).
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=10)

            with session_scope(self._session_factory) as session:
                mark_orchestration_run_terminal(
                    session,
                    run_id,
                    status="cancelled",
                    last_action=f"Cancelled: {reason}",
                    cancel_reason=reason,
                )
                row = get_latest_orchestration_run(session)
            return orchestration_run_to_dict(row)

    # ------------------------------------------------------------------
    # Restart recovery
    # ------------------------------------------------------------------

    def recover_on_startup(self) -> dict[str, Any]:
        """Reconcile durable state after a server restart.

        - Detects orphaned active orchestration runs (the previous process died).
        - Never launches a duplicate JobHunter or UAA run automatically.
        - Marks orphaned runs as ``failed`` with a durable reason.
        - Reuses WQ-5 recovery for stale UAA pipeline runs (already done in
          the app lifespan before this service is called).
        - Surfaces a safe manual recovery action in the run's last_error.
        """
        from universal_auto_applier.persistence.orchestration_run_repository import (
            list_active_orchestration_runs,
        )

        recovered: list[str] = []
        with session_scope(self._session_factory) as session:
            active_runs = list_active_orchestration_runs(session)
            for row in active_runs:
                reason = (
                    f"Orchestration run {row.run_id[:8]} was interrupted by a "
                    f"server restart. JobHunter child (pid={row.jobhunter_pid}) "
                    f"is no longer owned by this process. Manual recovery: "
                    f"review the run state and start a new orchestration run "
                    f"if needed. Nothing was auto-retried."
                )
                mark_orchestration_run_terminal(
                    session,
                    row.run_id,
                    status="failed",
                    last_action="Recovered after server restart; manual review required",
                    last_error=reason,
                )
                recovered.append(row.run_id)
        if recovered:
            logger.warning(
                "[orchestration] startup recovery: %d orphaned run(s) marked failed: %s",
                len(recovered),
                [run_id[:8] for run_id in recovered],
            )
        return {"recovered": recovered}

    # ------------------------------------------------------------------
    # Internal: run loop
    # ------------------------------------------------------------------

    def _run(
        self,
        run_id: str,
        mode: str,
        fixture_html: str | None,
        max_jobs: int,
    ) -> None:
        """Execute the orchestration run in a background thread."""
        try:
            if mode == "sequential":
                self._run_sequential(run_id, fixture_html, max_jobs)
            else:
                self._run_parallel(run_id, fixture_html, max_jobs)
        except Exception as exc:  # noqa: BLE001 - last-resort boundary
            logger.exception("[orchestration] run %s failed: %s", run_id[:8], exc)
            self._mark_failed(run_id, str(exc))
            return

        # If cancellation was requested, the cancel() method handles terminal
        # state. Otherwise mark completed (if not already terminal).
        if self._cancel_requested.is_set():
            return
        with session_scope(self._session_factory) as session:
            row = get_latest_orchestration_run(session)
        if row is not None and row.status in ACTIVE_STATUSES:
            self._mark_completed(run_id)

    def _run_sequential(self, run_id: str, fixture_html: str | None, max_jobs: int) -> None:
        """Sequential: JobHunter -> queue import -> UAA pipeline.

        Proves this exact order:
        1. validate configuration (done in start()).
        2. start JobHunter.
        3. wait for successful exit.
        4. verify queue file exists and is stable.
        5. call the existing WQ-3 queue-import service.
        6. start the existing WQ-4/WQ-5 safe browser pipeline.
        7. expose final results in durable orchestration status.
        """
        # Phase 1: JobHunter
        self._set_phase(run_id, "jobhunter_running", "Starting JobHunter export")
        queue_path = self._resolve_queue_output()
        self._runner = JobHunterRunner(
            settings=self._settings,
            queue_output_path=queue_path,
            entry_point=self._settings.jobhunter_entry_point,
            extra_args=self._jobhunter_extra_args,
        )
        pid = self._runner.launch()
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_pid=pid,
                jobhunter_started_at=_utcnow(),
                last_action=f"JobHunter subprocess launched (pid={pid})",
            )

        if self._cancel_requested.is_set():
            self._runner.cancel()
            return

        exit_code = self._runner.wait()
        result = self._runner.collect_result(exit_code)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_exit_code=exit_code,
                jobhunter_finished_at=_utcnow(),
                jobhunter_stdout=result.stdout,
                jobhunter_stderr=result.stderr,
                last_action=f"JobHunter exited with code {exit_code}",
            )

        if self._cancel_requested.is_set():
            return

        if exit_code != 0:
            self._mark_failed(
                run_id,
                f"JobHunter failed with exit code {exit_code}. "
                f"No queue import or pipeline started.",
            )
            return

        # Phase 2: verify queue file is stable
        self._set_phase(run_id, "verifying_queue", "Verifying queue file")
        if not self._wait_for_stable_queue(queue_path):
            self._mark_failed(
                run_id,
                f"Queue file not stable at {queue_path}. No import or pipeline started.",
            )
            return

        if self._cancel_requested.is_set():
            return

        # Phase 3: queue import
        self._set_phase(run_id, "importing", "Importing queue")
        try:
            summary = self._queue_import_service.run(path=queue_path, trigger="orchestration")
        except Exception as exc:  # noqa: BLE001 - import failure must not crash
            self._mark_failed(run_id, f"Queue import failed: {exc}")
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_import_run_id=summary.run_id,
                queue_import_state=summary.state,
                queue_imported=summary.imported,
                queue_skipped=summary.skipped,
                last_action=f"Queue import: {summary.state} ({summary.imported} imported)",
            )

        if self._cancel_requested.is_set():
            return

        # Phase 4: UAA pipeline
        self._set_phase(run_id, "pipeline_running", "Starting UAA pipeline")
        try:
            pipeline_state = self._pipeline_worker.start(
                max_jobs=max_jobs,
                fixture_html=fixture_html,
            )
        except RuntimeError as exc:
            self._mark_failed(run_id, f"Pipeline start failed: {exc}")
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                pipeline_run_id=pipeline_state.get("run_id"),
                pipeline_state=pipeline_state.get("status"),
                last_action=f"Pipeline started: {pipeline_state.get('status')}",
            )

        # Wait for the pipeline to reach a terminal state (poll durable state).
        self._wait_for_pipeline(run_id)

    def _run_parallel(self, run_id: str, fixture_html: str | None, max_jobs: int) -> None:
        """Parallel: UAA pipeline (existing jobs) + JobHunter concurrently.

        - Start the UAA pipeline for already queued eligible jobs and JobHunter
          concurrently.
        - Do not start two UAA pipeline workers.
        - When JobHunter finishes successfully, import the atomic queue once.
        - After import, schedule newly imported eligible jobs without
          reprocessing completed/recovered/terminal jobs.
        - Failure of JobHunter must not erase or invalidate UAA work already
          completed from the existing queue.
        """
        # Start JobHunter in a sub-thread so we can start UAA concurrently.
        queue_path = self._resolve_queue_output()
        self._runner = JobHunterRunner(
            settings=self._settings,
            queue_output_path=queue_path,
            entry_point=self._settings.jobhunter_entry_point,
            extra_args=self._jobhunter_extra_args,
        )

        jh_thread = threading.Thread(
            target=self._run_jobhunter,
            args=(run_id,),
            daemon=True,
            name=f"orchestration-jh-{run_id[:8]}",
        )

        # Phase 1: start JobHunter
        self._set_phase(run_id, "jobhunter_running", "Starting JobHunter export (parallel)")
        try:
            pid = self._runner.launch()
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(run_id, f"JobHunter launch failed: {exc}")
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_pid=pid,
                jobhunter_started_at=_utcnow(),
                last_action=f"JobHunter subprocess launched (pid={pid})",
            )

        jh_thread.start()

        if self._cancel_requested.is_set():
            return

        # Phase 2: start UAA pipeline for existing jobs
        self._set_phase(run_id, "pipeline_running", "Starting UAA pipeline for existing jobs")
        try:
            pipeline_state = self._pipeline_worker.start(
                max_jobs=max_jobs,
                fixture_html=fixture_html,
            )
        except RuntimeError as exc:
            # Pipeline couldn't start (maybe no eligible jobs). That's OK in
            # parallel mode — we still wait for JobHunter and import.
            pipeline_state = {"run_id": None, "status": "idle", "error": str(exc)}
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    last_action=f"Pipeline not started: {exc}",
                )

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                pipeline_run_id=pipeline_state.get("run_id"),
                pipeline_state=pipeline_state.get("status"),
            )

        # Wait for JobHunter to finish.
        jh_thread.join(timeout=600)
        if jh_thread.is_alive():
            logger.warning("[orchestration] JobHunter thread did not finish in 600s")
            self._runner.cancel()
            jh_thread.join(timeout=30)

        if self._cancel_requested.is_set():
            return

        # Phase 3: import the queue (if JobHunter succeeded)
        # The _run_jobhunter method updated the run row with exit_code.
        with session_scope(self._session_factory) as session:
            row = get_latest_orchestration_run(session)
        if row is None:
            return
        if row.jobhunter_exit_code != 0:
            # JobHunter failed — do NOT import, do NOT erase UAA work.
            self._set_phase(
                run_id,
                "failed",
                f"JobHunter failed (exit {row.jobhunter_exit_code}); no import. UAA pipeline continues.",
            )
            # Wait for the pipeline to finish (it was already started).
            self._wait_for_pipeline(run_id)
            return

        # JobHunter succeeded: import the queue.
        self._set_phase(run_id, "importing", "Importing queue (parallel)")
        try:
            summary = self._queue_import_service.run(path=queue_path, trigger="orchestration")
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(run_id, f"Queue import failed: {exc}")
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_import_run_id=summary.run_id,
                queue_import_state=summary.state,
                queue_imported=summary.imported,
                queue_skipped=summary.skipped,
                last_action=f"Queue import: {summary.state} ({summary.imported} imported)",
            )

        # Wait for the pipeline to finish. Newly imported eligible jobs are
        # NOT automatically picked up by the running pipeline (the pipeline
        # snapshots eligible jobs at start). A new pipeline start would be
        # needed to process them — but we do NOT auto-start a second pipeline
        # (only one active pipeline at a time). The operator can start a new
        # orchestration run or pipeline after this one completes.
        self._wait_for_pipeline(run_id)

    def _run_jobhunter(self, run_id: str) -> None:
        """Run JobHunter and update the run row (used in parallel mode)."""
        assert self._runner is not None
        exit_code = self._runner.wait()
        result = self._runner.collect_result(exit_code)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_exit_code=exit_code,
                jobhunter_finished_at=_utcnow(),
                jobhunter_stdout=result.stdout,
                jobhunter_stderr=result.stderr,
                last_action=f"JobHunter exited with code {exit_code}",
            )

    def _wait_for_stable_queue(self, path: Path, timeout: float = 5.0) -> bool:
        """Wait until the queue file exists and its size is stable.

        JobHunter writes the queue atomically (temp file + os.replace), so
        the file should appear as a complete, valid JSONL. This check ensures
        we never import a partially-written file.
        """
        import time

        deadline = time.monotonic() + timeout
        last_size: int = -1
        while time.monotonic() < deadline:
            if self._cancel_requested.is_set():
                return False
            if path.exists() and path.is_file():
                size = path.stat().st_size
                if size == last_size and size >= 0:
                    return True
                last_size = size
            time.sleep(0.1)
        return path.exists() and path.is_file()

    def _wait_for_pipeline(self, run_id: str, timeout: float = 300.0) -> None:
        """Poll the pipeline worker until it reaches a terminal state."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel_requested.is_set():
                return
            state = self._pipeline_worker.get_state_dict()
            status = state.get("status", "idle")
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    pipeline_state=status,
                    last_action=f"Pipeline: {status}",
                )
            if status in ("idle", "completed", "cancelled", "failed", "recovered"):
                return
            time.sleep(0.5)

    def _set_phase(self, run_id: str, phase: str, action: str) -> None:
        """Update the current phase and last action on the run row."""
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                current_phase=phase,
                status=phase if phase in ACTIVE_STATUSES else "running",
                last_action=action,
            )

    def _mark_failed(self, run_id: str, error: str) -> None:
        """Mark the run as failed with a durable error."""
        with session_scope(self._session_factory) as session:
            mark_orchestration_run_terminal(
                session,
                run_id,
                status="failed",
                last_action="Orchestration run failed",
                last_error=error,
            )

    def _mark_completed(self, run_id: str) -> None:
        """Mark the run as completed."""
        with session_scope(self._session_factory) as session:
            mark_orchestration_run_terminal(
                session,
                run_id,
                status="completed",
                last_action="Orchestration run completed",
            )

    def _validate_config(self, mode: str) -> None:
        """Validate that all required configuration is present."""
        if self._settings.jobhunter_repo is None:
            raise OrchestrationConfigurationError(
                "JobHunter repo path is not configured (set UAA_JOBHUNTER_REPO)"
            )
        repo = self._settings.jobhunter_repo
        if not repo.exists():
            raise OrchestrationConfigurationError(f"JobHunter repo path does not exist: {repo}")
        script = repo / self._settings.jobhunter_entry_point
        if not script.exists():
            raise OrchestrationConfigurationError(f"JobHunter entry point not found: {script}")
        # _resolve_queue_output raises if no path is configured.
        self._resolve_queue_output()

    def _resolve_queue_output(self) -> Path:
        """Resolve the queue output path.

        Priority:
        1. settings.jobhunter_queue_output (explicit override)
        2. settings.queue_path (the standard UAA queue path)
        """
        if self._settings.jobhunter_queue_output is not None:
            return self._settings.jobhunter_queue_output
        if self._settings.queue_path is not None:
            return self._settings.queue_path
        raise OrchestrationConfigurationError(
            "Queue output path is not configured (set UAA_QUEUE_PATH or UAA_JOBHUNTER_QUEUE_OUTPUT)"
        )


__all__ = [
    "OrchestrationConfigurationError",
    "OrchestrationConcurrentError",
    "OrchestrationService",
]
