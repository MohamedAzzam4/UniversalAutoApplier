"""JobHunter subprocess boundary (WQ-6).

Launches JobHunter as an external subprocess using its documented public
entry point. The production default is ``run_all.py`` (full workflow:
scan → evaluate/tailor → atomic queue export). For testing, the entry
point can be set to a fake producer script.

This module NEVER imports JobHunter Python modules — the boundary is
process-level only.

Safety:
- Never constructs a shell command string. Uses an argument list with
  :class:`subprocess.Popen`.
- Never places tokens, API keys, CV data, or candidate data in command-line
  arguments.
- ``run_all.py`` does NOT accept ``--output``; UAA reads the queue from the
  path JobHunter writes to (configured in JobHunter's profile.yml). Only
  ``run_export_queue.py`` (the standalone exporter) accepts ``--output``.
- Drains stdout and stderr CONCURRENTLY while the child is running using
  background threads. This prevents OS pipe-buffer deadlock when a noisy
  child fills the pipe before the parent reads it.
- Captures stdout/stderr with bounded storage (the caller configures the
  max bytes; the capture is truncated to that limit). Secrets are redacted
  before durable storage.
- The caller determines success from the process exit code plus the atomic
  queue file contract — never by parsing human-readable logs.
- On timeout: marks the result as timed out, requests graceful termination,
  force-kills after the configured grace period, reaps the process, joins
  drain threads, and persists the timeout outcome. The orchestration run
  becomes failed; no queue import occurs.
- Graceful termination is attempted first (SIGTERM/TerminateProcess); forced
  termination (SIGKILL) is used only after a bounded grace period.
- Never kills a process based solely on an unverified stale PID. Cancellation
  uses the :class:`subprocess.Popen` handle owned by this process.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_auto_applier.config import Settings

logger = logging.getLogger("universal_auto_applier.jobhunter_runner")

# Sentinel lines that should never appear in captured output (secrets filter).
# The filter is conservative: it redacts any line containing these patterns.
_SECRET_PATTERNS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "bearer",
    "openrouter",
    "google_ai",
    "telegram",
)

# Entry points that accept --output. Only run_export_queue.py supports it;
# run_all.py does NOT (it reads the path from config/profile.yml).
_ENTRY_POINTS_WITH_OUTPUT_FLAG = frozenset({"run_export_queue.py"})


@dataclass
class JobHunterResult:
    """The outcome of one JobHunter subprocess run."""

    exit_code: int | None
    stdout: str
    stderr: str
    pid: int
    started_at: float
    finished_at: float | None
    timed_out: bool = False
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "pid": self.pid,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
        }


@dataclass
class JobHunterRunner:
    """Launches and controls one JobHunter subprocess.

    The runner owns the :class:`subprocess.Popen` handle for the lifetime of
    the child process. Cancellation uses the handle directly — never a stale
    PID read from the database.
    """

    settings: Settings
    queue_output_path: Path
    # The entry point script name relative to the JobHunter repo root.
    entry_point: str = "run_all.py"
    # Optional extra args passed after --output. Used by tests to point at
    # fixture data inside the JobHunter repo (e.g. --evaluations, --pipeline).
    extra_args: list[str] = field(default_factory=lambda: list[str]())
    # The Popen handle (None until launched, or after the process exits).
    _proc: subprocess.Popen[str] | None = None
    _stdout_buf: list[str] = field(default_factory=lambda: list[str]())
    _stderr_buf: list[str] = field(default_factory=lambda: list[str]())
    _started_at: float = 0.0
    # Set by cancel() to signal wait() to stop polling.
    _cancel_requested: bool = False
    # Set by _handle_timeout() to indicate the last wait() timed out.
    _timed_out: bool = False
    # Drain threads for concurrent stdout/stderr reading.
    _drain_threads: list[threading.Thread] = field(default_factory=lambda: list[threading.Thread]())
    # Lock for drain thread buffer access.
    _buf_lock: threading.Lock = field(default_factory=threading.Lock)

    def validate(self) -> None:
        """Validate configuration before launching.

        Raises:
            RuntimeError: if any required path/executable is missing or invalid.
        """
        repo = self.settings.jobhunter_repo
        if repo is None:
            raise RuntimeError("JobHunter repo path is not configured (set UAA_JOBHUNTER_REPO)")
        if not repo.exists():
            raise RuntimeError(f"JobHunter repo path does not exist: {repo}")
        if not repo.is_dir():
            raise RuntimeError(f"JobHunter repo path is not a directory: {repo}")
        script = repo / self.entry_point
        if not script.exists():
            raise RuntimeError(
                f"JobHunter entry point not found: {script} "
                f"(set UAA_JOBHUNTER_ENTRY_POINT to the correct script name)"
            )
        python = self._resolve_python()
        if not python:
            raise RuntimeError(
                "Could not resolve a Python executable for JobHunter "
                "(set UAA_JOBHUNTER_PYTHON or run UAA with sys.executable)"
            )
        if not Path(python).exists():
            raise RuntimeError(f"JobHunter Python executable does not exist: {python}")
        if not self.queue_output_path.is_absolute():
            raise RuntimeError(
                f"Queue output path must be absolute, got {self.queue_output_path!r}"
            )
        # Ensure the parent directory exists so JobHunter can write the file.
        self.queue_output_path.parent.mkdir(parents=True, exist_ok=True)

    def _resolve_python(self) -> str:
        """Return the Python executable to use for JobHunter."""
        if self.settings.jobhunter_python:
            return self.settings.jobhunter_python
        return sys.executable

    def build_command(self) -> list[str]:
        """Build the argument list for :class:`subprocess.Popen`.

        Never constructs a shell command string. Returns a list of arguments
        suitable for ``Popen(args=...)`` with ``shell=False``.

        ``run_all.py`` does NOT accept ``--output``; it reads the queue path
        from JobHunter's ``config/profile.yml``. Only ``run_export_queue.py``
        accepts ``--output``. UAA reads the queue from
        :attr:`queue_output_path` after JobHunter exits.
        """
        repo = self.settings.jobhunter_repo
        if repo is None:
            raise RuntimeError("JobHunter repo path is not configured")
        python = self._resolve_python()
        script = str(repo / self.entry_point)
        cmd: list[str] = [python, script]
        # Only pass --output to entry points that support it.
        if self.entry_point in _ENTRY_POINTS_WITH_OUTPUT_FLAG:
            cmd += ["--output", str(self.queue_output_path)]
        cmd += self.extra_args
        return cmd

    def launch(self) -> int:
        """Launch the JobHunter subprocess and return its PID.

        Starts concurrent drain threads for stdout/stderr to prevent OS
        pipe-buffer deadlock.

        Raises:
            RuntimeError: if validation fails or the process cannot be started.
        """
        self.validate()
        cmd = self.build_command()
        repo = self.settings.jobhunter_repo
        assert repo is not None  # validated above
        env = self._build_env()
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(repo),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Do not use shell=True — we pass an argument list.
                shell=False,
                # Use a moderate buffer size to reduce thread contention.
                bufsize=-1,
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to launch JobHunter subprocess: {exc}") from exc
        self._started_at = time.time()
        self._timed_out = False
        # Start concurrent drain threads immediately to prevent pipe deadlock.
        self._start_drain_threads()
        logger.info(
            "[orchestration] JobHunter subprocess launched (pid=%s, repo=%s, entry=%s)",
            self._proc.pid,
            repo,
            self.entry_point,
        )
        return self._proc.pid

    def _start_drain_threads(self) -> None:
        """Start background threads to drain stdout and stderr concurrently.

        This prevents OS pipe-buffer deadlock: if the child writes more than
        the pipe capacity (typically 64KB on Linux) without the parent
        reading, the child blocks forever. The drain threads read
        continuously and accumulate into bounded buffers.
        """
        proc = self._proc
        if proc is None:
            return
        max_bytes = self.settings.orchestration_capture_max_bytes
        for stream, buf_attr in ((proc.stdout, "_stdout_buf"), (proc.stderr, "_stderr_buf")):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain_stream,
                args=(stream, buf_attr, max_bytes),
                daemon=True,
                name=f"jh-drain-{buf_attr}",
            )
            thread.start()
            self._drain_threads.append(thread)

    def _drain_stream(self, stream: Any, buf_attr: str, max_bytes: int) -> None:
        """Read one stream line by line into a bounded buffer.

        This runs in a background thread. It reads until EOF (process exits
        and closes the pipe), accumulating lines into the buffer up to
        ``max_bytes``. After the buffer is full, it continues reading and
        DISCARDING lines to prevent the OS pipe buffer from filling up and
        deadlocking the child process. Secrets are NOT filtered here
        (filtering happens at collect_result time) to keep the hot path fast.
        """
        buf: list[str] = []
        total = 0
        buffer_full = False
        try:
            for line in stream:
                if buffer_full:
                    # Buffer is full; discard the line but keep reading to
                    # prevent OS pipe deadlock.
                    continue
                encoded_len = len(line.encode("utf-8", errors="replace"))
                if total + encoded_len > max_bytes:
                    remaining = max_bytes - total
                    if remaining > 0:
                        buf.append(line[:remaining])
                    buf.append("\n... [output truncated]\n")
                    buffer_full = True
                    continue
                buf.append(line)
                total += encoded_len
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass
            with self._buf_lock:
                setattr(self, buf_attr, buf)

    def _build_env(self) -> dict[str, str]:
        """Build the environment for the JobHunter subprocess.

        The subprocess inherits the parent environment (so JobHunter can read
        its own .env file via python-dotenv). UAA never injects tokens, API
        keys, or candidate data into the child environment.
        """
        env = dict(os.environ)
        # Ensure the child knows it should be headless/non-interactive.
        env.setdefault("UAA_BROWSER_HEADLESS", "true")
        env.setdefault("UAA_SUBMIT_MODE", "review")
        env.setdefault("UAA_ENABLE_REAL_SUBMISSION", "false")
        return env

    @property
    def pid(self) -> int | None:
        """The child PID, or None if not launched / already exited."""
        proc = self._proc
        if proc is None:
            return None
        return proc.pid

    @property
    def is_alive(self) -> bool:
        """True if the child process is still running."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def was_timed_out(self) -> bool:
        """True if the last ``wait()`` call ended due to a timeout.

        When True, the process was terminated by the runner and the
        orchestration run should be marked failed (no import, no pipeline).
        """
        return self._timed_out

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the child to exit and return its exit code.

        Uses a poll loop so that ``cancel()`` can terminate the process from
        another thread. On timeout, the process is terminated, force-killed
        if necessary, reaped, and the result is marked as timed out.

        Returns the exit code, or None if the process was not launched.

        On any return path (normal exit, cancel, or timeout), the child has
        been waited on (``returncode`` is set) and pipes are closed so
        ``Popen.__del__`` will not emit ``ResourceWarning``.
        """
        proc = self._proc
        if proc is None:
            return None
        deadline = None
        if timeout is not None and timeout > 0:
            deadline = time.monotonic() + timeout
        while True:
            if self._cancel_requested:
                # Cancel was requested; let cancel() handle the cleanup.
                break
            rc = proc.poll()
            if rc is not None:
                self._join_drain_threads()
                self._close_pipes()
                return rc
            if deadline is not None and time.monotonic() >= deadline:
                # Timeout: terminate, reap, mark timed out.
                self._handle_timeout()
                return proc.returncode
            time.sleep(0.1)
        # Cancel was requested; cancel() handles cleanup. The caller MUST
        # invoke cancel() (or close()) to ensure the process is reaped and
        # pipes are closed before this runner is discarded.
        return proc.returncode

    def _handle_timeout(self) -> None:
        """Handle a timeout: terminate, force-kill if needed, reap, join drains.

        This ensures the child PID is no longer alive, pipes are drained and
        closed, and the orchestration run will be marked failed.
        """
        proc = self._proc
        if proc is None:
            return
        self._timed_out = True
        logger.warning(
            "[orchestration] JobHunter subprocess timed out (pid=%s); terminating",
            proc.pid,
        )
        self._terminate()
        try:
            proc.wait(timeout=self.settings.orchestration_cancel_grace_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[orchestration] JobHunter did not exit in %ss; force killing",
                self.settings.orchestration_cancel_grace_seconds,
            )
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._join_drain_threads()
        self._close_pipes()

    def _join_drain_threads(self) -> None:
        """Join the drain threads so they finish reading and close pipes."""
        for thread in self._drain_threads:
            if thread.is_alive():
                thread.join(timeout=5)
        self._drain_threads.clear()

    def _close_pipes(self) -> None:
        """Explicitly close ``proc.stdout`` and ``proc.stderr``.

        The drain threads already call ``stream.close()`` in their ``finally``
        block, but this is a belt-and-braces safety net so that
        ``Popen.__del__`` never observes open pipes when the runner is GC'd.
        Idempotent: silently skips if the pipe is already closed or None.
        """
        proc = self._proc
        if proc is None:
            return
        for attr in ("stdout", "stderr"):
            stream = getattr(proc, attr, None)
            if stream is None:
                continue
            try:
                if not stream.closed:
                    stream.close()
            except OSError:
                pass

    def close(self) -> None:
        """Idempotently reap the child and close all pipes.

        Safe to call multiple times. After this returns:
        - the child process has been waited on (``returncode`` is set);
        - drain threads have been joined;
        - ``proc.stdout`` / ``proc.stderr`` are closed.

        This is the contract every caller MUST satisfy before discarding the
        runner reference, otherwise ``Popen.__del__`` may emit
        ``ResourceWarning``.
        """
        proc = self._proc
        if proc is None:
            self._join_drain_threads()
            return
        if proc.poll() is None:
            # Still running: terminate, then reap.
            self._terminate()
            try:
                proc.wait(timeout=self.settings.orchestration_cancel_grace_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        self._join_drain_threads()
        self._close_pipes()

    def cancel(self) -> int | None:
        """Request graceful cancellation of the child process.

        Uses the owned :class:`Popen` handle — never a stale PID. Graceful
        termination (SIGTERM/TerminateProcess) is attempted first; forced
        termination (SIGKILL) is used only after the configured grace period.

        After this returns, the child has been waited on (``returncode`` is
        set) and pipes are closed.
        """
        self._cancel_requested = True
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is not None:
            # Already exited; join drain threads and close pipes.
            self._join_drain_threads()
            self._close_pipes()
            return proc.returncode
        logger.info("[orchestration] cancelling JobHunter subprocess (pid=%s)", proc.pid)
        self._terminate()
        try:
            proc.wait(timeout=self.settings.orchestration_cancel_grace_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[orchestration] JobHunter did not exit in %ss; force killing",
                self.settings.orchestration_cancel_grace_seconds,
            )
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._join_drain_threads()
        self._close_pipes()
        return proc.returncode

    def _terminate(self) -> None:
        """Send a graceful termination signal to the child."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            logger.warning("[orchestration] error sending SIGTERM to JobHunter")

    def _ensure_reaped(self) -> None:
        """Ensure the child process has been waited on (returncode is set).

        Deprecated alias for :meth:`close`. Retained for backward
        compatibility with callers that already use it.
        """
        self.close()

    def collect_result(
        self, exit_code: int | None, *, cancelled: bool = False, timed_out: bool = False
    ) -> JobHunterResult:
        """Build a :class:`JobHunterResult` from the current state."""
        with self._buf_lock:
            stdout_raw = "".join(self._stdout_buf)
            stderr_raw = "".join(self._stderr_buf)
        stdout = _filter_secrets(stdout_raw)
        stderr = _filter_secrets(stderr_raw)
        return JobHunterResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            pid=self.pid or 0,
            started_at=self._started_at,
            finished_at=time.time(),
            cancelled=cancelled,
            timed_out=timed_out,
        )


def _filter_secrets(text: str) -> str:
    """Redact lines that look like they contain secrets.

    The filter is conservative: any line containing one of the secret patterns
    (case-insensitive) is replaced with ``[redacted]``. This is a safety net —
    JobHunter should never log secrets, but UAA defends in depth.
    """
    if not text:
        return ""
    lines = text.splitlines(keepends=True)
    filtered: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(pattern in lower for pattern in _SECRET_PATTERNS):
            # Keep the line break but redact the content.
            filtered.append("[redacted]\n")
        else:
            filtered.append(line)
    return "".join(filtered)


__all__ = [
    "JobHunterResult",
    "JobHunterRunner",
]
