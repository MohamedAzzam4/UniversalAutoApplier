"""JobHunter subprocess boundary (WQ-6).

Launches JobHunter as an external subprocess using its documented public
entry point (``python run_export_queue.py --output <path>``). This module
NEVER imports JobHunter Python modules — the boundary is process-level only.

Safety:
- Never constructs a shell command string. Uses an argument list with
  :class:`subprocess.Popen`.
- Never places tokens, API keys, CV data, or candidate data in command-line
  arguments (only the queue output path is passed, which is a local file).
- Captures stdout/stderr with bounded storage (the caller configures the
  max bytes; the capture is truncated to that limit).
- The caller determines success from the process exit code plus the atomic
  queue file contract — never by parsing human-readable logs.
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
    entry_point: str = "run_export_queue.py"
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
        """
        repo = self.settings.jobhunter_repo
        if repo is None:
            raise RuntimeError("JobHunter repo path is not configured")
        python = self._resolve_python()
        script = str(repo / self.entry_point)
        # Only the queue output path is passed on the command line. No tokens,
        # API keys, CV data, or candidate data are ever placed in args.
        return [
            python,
            script,
            "--output",
            str(self.queue_output_path),
            *self.extra_args,
        ]

    def launch(self) -> int:
        """Launch the JobHunter subprocess and return its PID.

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
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to launch JobHunter subprocess: {exc}") from exc
        self._started_at = time.time()
        logger.info(
            "[orchestration] JobHunter subprocess launched (pid=%s, repo=%s)",
            self._proc.pid,
            repo,
        )
        return self._proc.pid

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

    def wait(self, timeout: float | None = None) -> int | None:
        """Wait for the child to exit and return its exit code.

        Captures stdout/stderr into bounded buffers with secret filtering.
        Returns None if the process was not launched.

        Uses a poll loop instead of ``communicate()`` so that ``cancel()``
        can terminate the process from another thread (the poll loop
        detects the exit and returns).
        """
        proc = self._proc
        if proc is None:
            return None
        deadline = None
        if timeout is not None:
            import time as _time

            deadline = _time.monotonic() + timeout
        # Poll until the process exits. This allows cancel() (from another
        # thread) to terminate the process; this loop will detect the exit.
        while True:
            if self._cancel_requested:
                # Cancel was requested; let cancel() handle the cleanup.
                break
            rc = proc.poll()
            if rc is not None:
                self._drain_and_store()
                return rc
            if deadline is not None:
                import time as _time

                if _time.monotonic() >= deadline:
                    self._drain_and_store()
                    return proc.returncode
            # Check for cancellation with a short sleep.
            if self._cancel_requested:
                break
            import time as _time

            _time.sleep(0.1)
        # If we get here, cancel was requested. The cancel() method will
        # call communicate() to drain and wait. Return the current returncode.
        return proc.returncode

    def cancel(self) -> int | None:
        """Request graceful cancellation of the child process.

        Uses the owned :class:`Popen` handle — never a stale PID. Graceful
        termination (SIGTERM/TerminateProcess) is attempted first; forced
        termination (SIGKILL) is used only after the configured grace period.
        """
        self._cancel_requested = True
        proc = self._proc
        if proc is None:
            return None
        if proc.poll() is not None:
            # Already exited; drain any remaining output.
            self._drain_and_store()
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
        self._drain_and_store()
        return proc.returncode

    def _drain_and_store(self) -> None:
        """Drain remaining stdout/stderr from the process and store it.

        Uses ``communicate()`` to safely read any remaining output after the
        process has exited. This is safe to call after ``wait()`` or
        ``cancel()`` because the process has already exited.
        """
        proc = self._proc
        if proc is None:
            return
        try:
            stdout_data, stderr_data = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            stdout_data, stderr_data = "", ""
        except OSError:
            stdout_data, stderr_data = "", ""
        self._store_output(str(stdout_data or ""), str(stderr_data or ""))

    def _store_output(self, stdout_str: str, stderr_str: str) -> None:
        """Truncate and store captured output in the buffers."""
        max_bytes = self.settings.orchestration_capture_max_bytes
        if len(stdout_str.encode("utf-8")) > max_bytes:
            stdout_str = stdout_str[:max_bytes] + "\n... [output truncated]\n"
        if len(stderr_str.encode("utf-8")) > max_bytes:
            stderr_str = stderr_str[:max_bytes] + "\n... [output truncated]\n"
        self._stdout_buf = [stdout_str]
        self._stderr_buf = [stderr_str]

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

        This is a safety net called during shutdown to prevent
        ResourceWarning from Popen.__del__ when the Popen handle is GC'd.
        If the process is still running, terminate it first.
        """
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            self._terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            except OSError:
                pass

    def collect_result(self, exit_code: int | None, *, cancelled: bool = False) -> JobHunterResult:
        """Build a :class:`JobHunterResult` from the current state."""
        stdout = _filter_secrets("".join(self._stdout_buf))
        stderr = _filter_secrets("".join(self._stderr_buf))
        return JobHunterResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            pid=self.pid or 0,
            started_at=self._started_at,
            finished_at=time.time(),
            cancelled=cancelled,
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
