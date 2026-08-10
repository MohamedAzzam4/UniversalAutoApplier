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
    list_active_orchestration_runs,
    mark_orchestration_run_terminal,
    orchestration_run_to_dict,
    update_orchestration_run,
)
from universal_auto_applier.services.jobhunter_runner import JobHunterRunner

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
            # before starting a new one.
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
        - For runs with a persisted JobHunter PID, conservatively checks PID
          liveness. A PID that is alive (owned by this process or a child)
          is kept active and blocks duplicate start. A dead/missing PID is
          marked failed.
        - Never launches a duplicate JobHunter or UAA run automatically.
        - Marks orphaned runs as ``failed`` with a durable reason.
        - Reuses WQ-5 recovery for stale UAA pipeline runs (already done in
          the app lifespan before this service is called).
        - Surfaces a safe manual recovery action in the run's last_error.
        """
        from universal_auto_applier.services.pipeline_recovery_service import pid_is_alive

        recovered: list[str] = []
        healthy_kept: list[str] = []
        with session_scope(self._session_factory) as session:
            active_runs = list_active_orchestration_runs(session)
            for row in active_runs:
                # If the run has a persisted JobHunter PID and it's alive,
                # keep the run active (don't recover it). This is conservative:
                # we can't verify we OWN the process, but we don't mark it
                # failed either. The operator can cancel it manually.
                if row.jobhunter_pid is not None and pid_is_alive(row.jobhunter_pid):
                    healthy_kept.append(row.run_id)
                    logger.info(
                        "[orchestration] startup recovery: run %s has live PID %s; keeping active",
                        row.run_id[:8],
                        row.jobhunter_pid,
                    )
                    continue
                reason = (
                    f"Orchestration run {row.run_id[:8]} was interrupted by a "
                    f"server restart. JobHunter child (pid={row.jobhunter_pid}) "
                    f"is no longer alive. Manual recovery: "
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
        if healthy_kept:
            logger.info(
                "[orchestration] startup recovery: %d run(s) with live PID kept active: %s",
                len(healthy_kept),
                [run_id[:8] for run_id in healthy_kept],
            )
        return {"recovered": recovered, "healthy_kept": healthy_kept}

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
        2. start JobHunter (full workflow: scan → evaluate/tailor → export).
        3. wait for successful exit.
        4. verify queue file exists and is stable.
        5. call the existing WQ-3 queue-import service.
        6. start the existing WQ-4/WQ-5 safe browser pipeline.
        7. expose final results in durable orchestration status.

        If JobHunter fails or times out, no import occurs and no pipeline starts.
        """
        # Phase 1: JobHunter
        self._set_phase(run_id, "jobhunter_running", "Starting JobHunter full workflow")
        queue_path = self._resolve_queue_output()
        # Capture queue signature BEFORE JobHunter for publication detection.
        q_hash_before, q_mtime_before = self._queue_file_signature(queue_path)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_hash_before=q_hash_before,
                queue_mtime_ns_before=q_mtime_before,
            )
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
                last_action=f"JobHunter subprocess launched (pid={pid}, entry={self._settings.jobhunter_entry_point})",
            )

        if self._cancel_requested.is_set():
            self._runner.cancel()
            return

        # Wait for JobHunter with the configured timeout.
        timeout = self._settings.jobhunter_timeout_seconds or None
        exit_code = self._runner.wait(timeout=timeout)
        timed_out = self._runner.was_timed_out
        result = self._runner.collect_result(exit_code, timed_out=timed_out)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_exit_code=exit_code,
                jobhunter_finished_at=_utcnow(),
                jobhunter_stdout=result.stdout,
                jobhunter_stderr=result.stderr,
                last_action=f"JobHunter exited with code {exit_code}"
                + (" (timed out)" if timed_out else ""),
            )

        if self._cancel_requested.is_set():
            return

        # If JobHunter timed out or failed, do NOT import or start pipeline.
        if timed_out:
            self._mark_failed(
                run_id,
                f"JobHunter timed out after {timeout}s and was terminated. "
                f"No queue import or pipeline started.",
            )
            return

        if exit_code != 0:
            self._mark_failed(
                run_id,
                f"JobHunter failed with exit code {exit_code}. "
                f"No queue import or pipeline started.",
            )
            return

        # Phase 2: verify queue file is stable and was actually published.
        self._set_phase(run_id, "verifying_queue", "Verifying queue file")
        q_hash_after, q_mtime_after = self._queue_file_signature(queue_path)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_hash_after=q_hash_after,
                queue_mtime_ns_after=q_mtime_after,
            )
        if not self._was_queue_published(run_id):
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    queue_published=False,
                    last_action="JobHunter succeeded but no new queue was published; no import",
                )
            return
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_published=True,
            )
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

        Required sequence:
        1. Start UAA processing for jobs already eligible.
        2. Start JobHunter concurrently.
        3. Wait for JobHunter's successful exit and atomic queue publication.
        4. Import the queue exactly once.
        5. Wait for the initial UAA pipeline run to reach a terminal state.
        6. If the import created newly eligible jobs, start exactly one second
           safe UAA pipeline pass for those jobs.
        7. Wait for that pass and persist its run id and outcome.
        8. Do not start a second pass when the import produced zero new eligible jobs.
        9. Do not reprocess jobs handled by the initial pass.
        10. Remain dry-run/review-only.
        """
        queue_path = self._resolve_queue_output()
        # Capture queue signature BEFORE JobHunter for publication detection.
        q_hash_before, q_mtime_before = self._queue_file_signature(queue_path)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_hash_before=q_hash_before,
                queue_mtime_ns_before=q_mtime_before,
            )
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
        self._set_phase(run_id, "jobhunter_running", "Starting JobHunter full workflow (parallel)")
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
                last_action=f"JobHunter subprocess launched (pid={pid}, entry={self._settings.jobhunter_entry_point})",
            )

        jh_thread.start()

        if self._cancel_requested.is_set():
            return

        # Phase 2: start UAA pipeline for existing jobs (initial pass)
        self._set_phase(run_id, "pipeline_running", "Starting UAA pipeline for existing jobs")
        initial_pipeline_state: dict[str, Any] | None = None
        try:
            initial_pipeline_state = self._pipeline_worker.start(
                max_jobs=max_jobs,
                fixture_html=fixture_html,
            )
        except RuntimeError as exc:
            # Pipeline couldn't start (maybe no eligible jobs). That's OK in
            # parallel mode — we still wait for JobHunter and import.
            initial_pipeline_state = None
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    last_action=f"Initial pipeline not started: {exc}",
                )

        if initial_pipeline_state is not None:
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    pipeline_run_id_initial=initial_pipeline_state.get("run_id"),
                    pipeline_state_initial=initial_pipeline_state.get("status"),
                )

        # Wait for JobHunter to finish.
        jh_timeout = self._settings.jobhunter_timeout_seconds or 600
        jh_thread.join(timeout=jh_timeout + 30)
        if jh_thread.is_alive():
            logger.warning(
                "[orchestration] JobHunter thread did not finish in %ss", jh_timeout + 30
            )
            self._runner.cancel()
            jh_thread.join(timeout=30)

        if self._cancel_requested.is_set():
            return

        # Check JobHunter exit code.
        with session_scope(self._session_factory) as session:
            row = get_latest_orchestration_run(session)
        if row is None:
            return
        if row.jobhunter_exit_code != 0:
            # JobHunter failed or timed out — do NOT import, do NOT erase UAA work.
            # Wait for the initial pipeline to finish.
            if initial_pipeline_state is not None:
                self._wait_for_pipeline_initial(run_id)
            self._mark_failed(
                run_id,
                f"JobHunter failed (exit {row.jobhunter_exit_code}); no import. UAA initial pipeline completed.",
            )
            return

        # Phase 3: Wait for the initial pipeline to reach terminal state.
        if initial_pipeline_state is not None:
            self._set_phase(run_id, "pipeline_running", "Waiting for initial UAA pipeline pass")
            self._wait_for_pipeline_initial(run_id)

        if self._cancel_requested.is_set():
            return

        # Phase 4: import the queue (JobHunter succeeded).
        # Detect whether a new queue was actually published during this run.
        q_hash_after, q_mtime_after = self._queue_file_signature(queue_path)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_hash_after=q_hash_after,
                queue_mtime_ns_after=q_mtime_after,
            )

        # Check if the queue was actually published (hash changed or file appeared).
        if not self._was_queue_published(run_id):
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    queue_published=False,
                    last_action="JobHunter succeeded but no new queue was published; no import",
                )
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_published=True,
            )

        # Snapshot eligible IDs BEFORE import for exact newly-eligible computation.
        eligible_before = self._snapshot_eligible_ids()

        self._set_phase(run_id, "importing", "Importing queue (parallel)")
        try:
            summary = self._queue_import_service.run(path=queue_path, trigger="orchestration")
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(run_id, f"Queue import failed: {exc}")
            return

        # Snapshot eligible IDs AFTER import and compute the exact newly eligible set.
        eligible_after = self._snapshot_eligible_ids()
        new_ids, new_count = self._compute_newly_eligible(eligible_before, eligible_after)

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                queue_import_run_id=summary.run_id,
                queue_import_state=summary.state,
                queue_imported=summary.imported,
                queue_skipped=summary.skipped,
                newly_eligible_count=new_count,
                newly_eligible_ids_json=new_ids,
                last_action=f"Queue import: {summary.state} ({summary.imported} imported, {new_count} newly eligible)",
            )

        if self._cancel_requested.is_set():
            return

        # Phase 5: If the import created newly eligible jobs, start a second
        # pipeline pass for those jobs. Do not start a second pass if zero
        # new eligible jobs were imported.
        if new_count == 0:
            with session_scope(self._session_factory) as session:
                update_orchestration_run(
                    session,
                    run_id,
                    last_action="No newly eligible jobs after import; no second pipeline pass",
                )
            return

        self._set_phase(
            run_id, "pipeline_running", "Starting second UAA pipeline pass for newly imported jobs"
        )
        try:
            second_pipeline_state = self._pipeline_worker.start(
                max_jobs=max_jobs,
                fixture_html=fixture_html,
            )
        except RuntimeError as exc:
            # Second pass failure is a HARD orchestration failure: import
            # succeeded but processing the newly eligible jobs did not start.
            # Imported jobs remain safely eligible for manual retry.
            self._mark_failed(
                run_id,
                f"Import succeeded ({summary.imported} imported, {new_count} newly eligible) "
                f"but the second pipeline pass failed to start: {exc}. "
                f"The newly imported jobs remain eligible for manual retry.",
            )
            return

        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                pipeline_run_id=second_pipeline_state.get("run_id"),
                pipeline_state=second_pipeline_state.get("status"),
                last_action=f"Second pipeline pass started: {second_pipeline_state.get('status')}",
            )

        # Wait for the second pipeline pass to reach terminal state.
        self._wait_for_pipeline(run_id)

    def _run_jobhunter(self, run_id: str) -> None:
        """Run JobHunter and update the run row (used in parallel mode)."""
        assert self._runner is not None
        timeout = self._settings.jobhunter_timeout_seconds or None
        exit_code = self._runner.wait(timeout=timeout)
        timed_out = self._runner.was_timed_out
        result = self._runner.collect_result(exit_code, timed_out=timed_out)
        with session_scope(self._session_factory) as session:
            update_orchestration_run(
                session,
                run_id,
                jobhunter_exit_code=exit_code,
                jobhunter_finished_at=_utcnow(),
                jobhunter_stdout=result.stdout,
                jobhunter_stderr=result.stderr,
                last_action=f"JobHunter exited with code {exit_code}"
                + (" (timed out)" if timed_out else ""),
            )

    def _count_newly_eligible_jobs(self) -> int:
        """Count jobs in READY_TO_APPLY or QUEUED status (eligible for pipeline).

        Deprecated: use ``_snapshot_eligible_ids`` + ``_compute_newly_eligible``
        for exact evidence. This method is retained for backward compatibility.
        """
        return len(self._snapshot_eligible_ids())

    def _snapshot_eligible_ids(self) -> set[str]:
        """Snapshot the set of application IDs currently eligible for the pipeline.

        Eligible = READY_TO_APPLY or QUEUED. Returns a set of application_id
        strings (SHA-256 hashes, never candidate data).
        """
        from sqlalchemy import select

        from universal_auto_applier.core.statuses import ApplicationStatus
        from universal_auto_applier.persistence.models import ApplicationJobRow

        with session_scope(self._session_factory) as session:
            rows = (
                session.execute(
                    select(ApplicationJobRow.application_id).where(
                        ApplicationJobRow.status.in_(
                            [
                                ApplicationStatus.READY_TO_APPLY.value,
                                ApplicationStatus.QUEUED.value,
                            ]
                        )
                    )
                )
                .scalars()
                .all()
            )
        return set(rows)

    @staticmethod
    def _compute_newly_eligible(before: set[str], after: set[str]) -> tuple[list[str], int]:
        """Compute the exact newly eligible application IDs from before/after snapshots.

        Returns (sorted_list_of_new_ids, count).
        """
        new_ids = after - before
        return sorted(new_ids), len(new_ids)

    @staticmethod
    def _hash_queue_file(path: Path) -> str | None:
        """Return the SHA-256 hex digest of the queue file, or None if missing.

        Uses the same algorithm as the queue-import service's fingerprint.
        """
        import hashlib

        try:
            if not path.exists() or not path.is_file():
                return None
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    @staticmethod
    def _queue_file_signature(path: Path) -> tuple[str | None, int | None]:
        """Return (content_hash, mtime_ns) of the queue file, or (None, None).

        The mtime_ns is used to detect whether the file was written during
        the JobHunter run. ``os.replace`` always updates mtime, so a
        re-exported identical queue is correctly detected as a publication.
        A stale pre-existing file (JobHunter didn't export) has an unchanged
        mtime and is NOT treated as a publication.
        """
        import hashlib

        try:
            if not path.exists() or not path.is_file():
                return None, None
            stat = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest(), stat.st_mtime_ns
        except OSError:
            return None, None

    def _was_queue_published(self, run_id: str) -> bool:
        """Check whether the queue file was actually published during this run.

        A publication is detected when:
        - The mtime_ns after JobHunter differs from the mtime_ns before, OR
        - The file did not exist before but exists after (hash None → not-None).

        An unchanged pre-existing queue (same mtime_ns) is NOT a publication.
        Content hash is recorded for durable evidence but mtime_ns is the
        primary detector because ``os.replace`` always updates it, even for
        identical content (idempotent re-export).
        """
        with session_scope(self._session_factory) as session:
            row = get_latest_orchestration_run(session)
        if row is None:
            return False
        before_hash = row.queue_hash_before
        after_hash = row.queue_hash_after
        before_mtime = row.queue_mtime_ns_before
        after_mtime = row.queue_mtime_ns_after
        # File appeared (was None before, exists after).
        if before_hash is None and after_hash is not None:
            return True
        # mtime changed (file was written during this run, even with same content).
        if before_mtime is not None and after_mtime is not None and before_mtime != after_mtime:
            return True
        return False

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
        """Poll the pipeline worker until it reaches a terminal state.

        Updates ``pipeline_state`` (the second-pass / sequential pipeline)
        on the orchestration run row.
        """
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

    def _wait_for_pipeline_initial(self, run_id: str, timeout: float = 300.0) -> None:
        """Poll the pipeline worker until it reaches a terminal state.

        Updates ``pipeline_state_initial`` (the first-pass / initial pipeline)
        on the orchestration run row.
        """
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
                    pipeline_state_initial=status,
                    last_action=f"Initial pipeline: {status}",
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
        1. settings.jobhunter_queue_output (explicit absolute override)
        2. <jobhunter_repo>/data/application_queue.jsonl (JobHunter default)
        3. settings.queue_path (the standard UAA queue path, for backward compat)

        ``run_all.py`` writes to the path configured in JobHunter's
        ``config/profile.yml`` → ``queue_export.output_path``, which defaults
        to ``data/application_queue.jsonl`` relative to the JobHunter repo
        root. UAA reads the queue from that same path after JobHunter exits.
        """
        if self._settings.jobhunter_queue_output is not None:
            return self._settings.jobhunter_queue_output
        repo = self._settings.jobhunter_repo
        if repo is not None:
            # Default: <jobhunter_repo>/data/application_queue.jsonl
            return repo / "data" / "application_queue.jsonl"
        if self._settings.queue_path is not None:
            return self._settings.queue_path
        raise OrchestrationConfigurationError(
            "Queue output path is not configured (set UAA_JOBHUNTER_REPO or UAA_JOBHUNTER_QUEUE_OUTPUT)"
        )


__all__ = [
    "OrchestrationConfigurationError",
    "OrchestrationConcurrentError",
    "OrchestrationService",
]
