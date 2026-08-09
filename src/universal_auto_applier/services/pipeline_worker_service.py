"""Background pipeline worker service (subprocess launcher).

WQ-4: the browser pipeline runs in its own OS subprocess
(:mod:`~.services.pipeline_worker_runner`), never inside a FastAPI request
thread. This app-scoped service (registered on ``app.state.pipeline_worker``):

* creates the durable ``pipeline_runs`` row for a new run synchronously,
* launches the worker subprocess against the same SQLite database,
* persists every control transition (pause/resume/cancel) through
  :mod:`pipeline_run_repository`,
* never touches Playwright and never performs final submission.

Pause / cancel are honored by the worker at job boundaries by re-reading the
run row. Only one active run is allowed (enforced by the repository's
``ACTIVE_STATUSES`` plus an in-process lock).

Safety:
- ``UAA_ENABLE_REAL_SUBMISSION`` is forced to ``false`` in the worker's
  environment regardless of the server's setting — a pipeline run can never
  submit.
- If the app restarts while a run is active and no live worker is detected,
  :meth:`cancel` marks the run terminal directly (recovery), and
  :meth:`start` refuses to create a duplicate run.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import universal_auto_applier
from universal_auto_applier.config import Settings
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.pipeline_run_repository import (
    create_pipeline_run,
    get_active_pipeline_run,
    get_latest_pipeline_run,
    mark_pipeline_run_terminal,
    pipeline_run_to_dict,
    update_pipeline_run,
)

logger = logging.getLogger("universal_auto_applier.pipeline_worker_service")


class PipelineWorkerService:
    """Starts and controls the background pipeline worker subprocess.

    App-scoped service registered on ``app.state.pipeline_worker``.
    Only one active pipeline run is allowed at a time.
    """

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._run_id: str | None = None
        self._drain_threads: list[threading.Thread] = []

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    def get_state_dict(self) -> dict[str, Any]:
        """Return the latest run state (durable, idle when none exists)."""
        with session_scope(self._session_factory) as session:
            row = get_latest_pipeline_run(session)
        return pipeline_run_to_dict(row)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        max_jobs: int = 10,
        fixture_html: str | None = None,
    ) -> dict[str, Any]:
        """Start a new pipeline run in a subprocess. Returns immediately.

        Raises RuntimeError if a run is already active.
        """
        with self._lock:
            with session_scope(self._session_factory) as session:
                active = get_active_pipeline_run(session)
            if active is not None:
                raise RuntimeError(
                    f"A pipeline run is already active: {active.run_id} ({active.status})"
                )

            fixture_file = self._prepare_fixture(fixture_html)

            # Wait for any previous worker subprocess to fully exit before
            # replacing _proc. This prevents the old Popen handle from being
            # GC'd while its child is still running (ResourceWarning). The
            # drain threads have already read the output; we only need to
            # set returncode via wait().
            prev_proc = self._proc
            if prev_proc is not None and prev_proc.returncode is None:
                try:
                    prev_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    prev_proc.terminate()
                    prev_proc.wait(timeout=5)
                except OSError:
                    pass

            run_id = str(uuid.uuid4())
            mode = "fixture_dry_run" if fixture_html else "sequential_dry_run"
            with session_scope(self._session_factory) as session:
                create_pipeline_run(
                    session,
                    run_id=run_id,
                    status="running",
                    mode=mode,
                )

            cmd = [
                sys.executable,
                "-m",
                "universal_auto_applier.services.pipeline_worker_runner",
                "--data-dir",
                str(self._settings.data_dir),
                "--run-id",
                run_id,
                "--max-jobs",
                str(max(1, max_jobs)),
                "--job-pulse-ms",
                str(self._settings.pipeline_job_pulse_ms),
            ]
            if fixture_file is not None:
                cmd += ["--fixture-file", str(fixture_file)]

            env = self._worker_env()
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                # The run row exists but the worker never started. Mark it
                # failed so the status API does not report a ghost "running".
                with session_scope(self._session_factory) as session:
                    mark_pipeline_run_terminal(
                        session,
                        run_id,
                        status="failed",
                        last_action=f"Failed to launch worker: {exc}",
                        last_error=str(exc),
                    )
                raise RuntimeError(f"Failed to launch pipeline worker: {exc}") from exc

            # WQ-5 liveness: record the worker's pid and start time on the
            # run row so startup recovery can distinguish a run owned by a
            # live process from a stale one. The worker refreshes heartbeat_at
            # continuously (including while paused/waiting).
            now = datetime.now(UTC)
            with session_scope(self._session_factory) as session:
                update_pipeline_run(
                    session,
                    run_id,
                    worker_pid=self._proc.pid,
                    worker_started_at=now,
                    heartbeat_at=now,
                )

            self._drain_output()
            self._run_id = run_id
            logger.info(
                "[pipeline] worker started (run=%s pid=%s)",
                run_id[:8],
                self._proc.pid,
            )
            return self.get_state_dict()

    def pause(self) -> dict[str, Any]:
        """Request pause. The worker finishes the current job, then pauses."""
        with self._lock:
            if not self._proc_alive():
                raise RuntimeError("Cannot pause: no live pipeline worker")
            with session_scope(self._session_factory) as session:
                row = get_active_pipeline_run(session)
            if row is None:
                raise RuntimeError("Cannot pause: no active pipeline run")
            if row.status != "running":
                raise RuntimeError(f"Cannot pause: pipeline is {row.status}")
            with session_scope(self._session_factory) as session:
                update_pipeline_run(
                    session,
                    row.run_id,
                    status="pausing",
                    last_action="Pause requested",
                )
            return self.get_state_dict()

    def resume(self) -> dict[str, Any]:
        """Resume a paused pipeline."""
        with self._lock:
            if not self._proc_alive():
                raise RuntimeError("Cannot resume: no live pipeline worker")
            with session_scope(self._session_factory) as session:
                row = get_active_pipeline_run(session)
            if row is None or row.status != "paused":
                raise RuntimeError("Cannot resume: pipeline is not paused")
            with session_scope(self._session_factory) as session:
                update_pipeline_run(
                    session,
                    row.run_id,
                    status="running",
                    last_action="Resumed",
                )
            return self.get_state_dict()

    def cancel(self, *, reason: str = "User cancelled") -> dict[str, Any]:
        """Request cancellation. The worker stops before the next job.

        If no live worker is running (e.g. the app restarted mid-run), the
        run is marked cancelled directly — the recovery path for WQ-4.
        """
        with self._lock:
            with session_scope(self._session_factory) as session:
                row = get_active_pipeline_run(session)
            if row is None:
                # Nothing active to cancel; return the current state.
                return self.get_state_dict()
            if self._proc_alive():
                with session_scope(self._session_factory) as session:
                    update_pipeline_run(
                        session,
                        row.run_id,
                        status="cancelling",
                        cancel_reason=reason,
                        last_action=f"Cancel requested: {reason}",
                    )
            else:
                logger.warning(
                    "[pipeline] run %s has no live worker; marking cancelled directly",
                    row.run_id[:8],
                )
                with session_scope(self._session_factory) as session:
                    mark_pipeline_run_terminal(
                        session,
                        row.run_id,
                        status="cancelled",
                        last_action=f"Cancelled: {reason}",
                    )
            return self.get_state_dict()

    def shutdown(self) -> None:
        """Terminate a running worker subprocess (best-effort).

        Called on app shutdown. The run row is left in its current state —
        WQ-5 owns stale-run recovery. On the next :meth:`start`, any stale
        active row causes a 409; :meth:`cancel` then recovers it.
        """
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            logger.info("[pipeline] terminating worker subprocess (pid=%s)", proc.pid)
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            except OSError:
                logger.warning("[pipeline] error terminating worker subprocess")
        # Wait for the drain threads to finish. After the process exits,
        # the pipes reach EOF and the drain threads exit on their own.
        for thread in self._drain_threads:
            if thread.is_alive():
                thread.join(timeout=5)
        self._drain_threads.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _proc_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def _prepare_fixture(self, fixture_html: str | None) -> Path | None:
        """Persist fixture HTML to a file read by the worker subprocess."""
        if not fixture_html:
            return None
        fixture_root = self._settings.data_dir / "fixtures"
        fixture_root.mkdir(parents=True, exist_ok=True)
        path = fixture_root / f"run-{uuid.uuid4().hex[:8]}.html"
        path.write_text(fixture_html, encoding="utf-8")
        return path

    def _drain_output(self) -> None:
        """Drain worker stdout/stderr in background threads (log only).

        Prevents the subprocess pipe buffers from filling and avoids
        ``ResourceWarning`` on unclosed pipes when the Popen object is
        garbage-collected.
        """
        proc = self._proc
        if proc is None:
            return
        for stream, level in ((proc.stdout, logger.info), (proc.stderr, logger.error)):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain_stream,
                args=(stream, level),
                daemon=True,
                name=f"pipeline-worker-drain-{id(stream)}",
            )
            thread.start()
            self._drain_threads.append(thread)

    def _drain_stream(self, stream: Any, level: Any) -> None:
        """Read one subprocess stream line by line into the logger."""
        try:
            for line in stream:
                if isinstance(line, str):
                    level("[pipeline-worker] %s", line.rstrip("\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _worker_env(self) -> dict[str, str]:
        """Environment for the worker subprocess.

        Safety: submission is forced off and the worker shares the app's data
        directory, so it reads and writes the same SQLite file.
        """
        env = dict(os.environ)
        src_dir = Path(universal_auto_applier.__file__).resolve().parent.parent
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else str(src_dir)
        env["UAA_DATA_DIR"] = str(self._settings.data_dir)
        env["UAA_BROWSER_HEADLESS"] = "true" if self._settings.browser_headless else "false"
        env["UAA_SUBMIT_MODE"] = "review"
        env["UAA_ENABLE_REAL_SUBMISSION"] = "false"
        env["UAA_BROWSER_TIMEOUT_MS"] = str(self._settings.browser_timeout_ms)
        env["UAA_BROWSER_MAX_STEPS"] = str(self._settings.browser_max_steps)
        if self._settings.browser_profile_dir is not None:
            env["UAA_BROWSER_PROFILE_DIR"] = str(self._settings.browser_profile_dir)
        if self._settings.browser_channel is not None:
            env["UAA_BROWSER_CHANNEL"] = self._settings.browser_channel
        if self._settings.candidate_profile is not None:
            env["UAA_CANDIDATE_PROFILE"] = str(self._settings.candidate_profile)
        return env


__all__ = ["PipelineWorkerService"]
