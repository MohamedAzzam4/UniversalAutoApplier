"""Siemens adapter — narrow boundary to SiemensAutoApplier.

Per ``ROADMAP.md`` WP 2.3 and ``ADR_001_ARCHITECTURE.md`` D4:

- ``SiemensAdapter`` lives inside UniversalAutoApplier.
- It invokes the existing Siemens workflow through a narrow integration
  boundary (CLI subprocess or importable entry point).
- It exchanges a typed request and a structured :class:`AdapterResult`; it
  does **not** parse human log text to determine success.
- Siemens selectors, page objects, and stage logic stay in
  SiemensAutoApplier. No code is copied.

Boundary mechanism:
The adapter invokes ``python main.py --job-id <id> --dry-run`` (or
``--no-dry-run``) as a subprocess. The subprocess exit code and the
structured ``JobResult`` dataclass in SiemensAutoApplier determine the
:class:`AdapterResult`. The adapter does NOT parse human-readable log
lines.

If ``UAA_SIEMENS_REPO`` is not configured, the adapter reports
``not_configured`` via the health endpoint and returns a structured
``AdapterResult`` with status ``blocked`` for any call.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import AdapterResult, ApplicationJob
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    Phase,
    Platform,
)

logger = logging.getLogger("universal_auto_applier.adapters.siemens")


@dataclass
class SiemensAdapterConfig:
    """Configuration for :class:`SiemensAdapter`.

    Attributes:
        repo_path: Absolute path to the SiemensAutoApplier repository.
            If None, the adapter is not configured and all calls return
            a ``blocked`` result.
        python_executable: Python executable to use when invoking the
            Siemens CLI. Defaults to ``sys.executable``.
        dry_run: If True, pass ``--dry-run`` to the Siemens CLI (safe mode,
            no actual submission). Defaults to True.
        headless: If True, pass ``--headless`` to the Siemens CLI.
            Defaults to True.
        timeout_seconds: Subprocess timeout. Defaults to 600 (10 minutes).
    """

    repo_path: Path | None = None
    python_executable: str = sys.executable
    dry_run: bool = True
    headless: bool = True
    timeout_seconds: int = 600

    @property
    def is_configured(self) -> bool:
        """Return True if the Siemens repo path is set and exists."""
        return self.repo_path is not None and self.repo_path.exists()


class SiemensAdapter(ApplicationAdapter):
    """Narrow boundary adapter for SiemensAutoApplier.

    This adapter does NOT contain any Siemens-specific selectors, page
    objects, or stage logic. It invokes the existing Siemens CLI as a
    subprocess and maps the structured result to :class:`AdapterResult`.
    """

    platform = Platform.SIEMENS

    def __init__(self, config: SiemensAdapterConfig | None = None) -> None:
        self._config = config or SiemensAdapterConfig()

    @property
    def config(self) -> SiemensAdapterConfig:
        return self._config

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the Siemens platform.

        Uses :func:`detect_platform` for deterministic hostname matching.
        Also returns True if the job's ``platform`` field is explicitly
        set to ``siemens``.
        """
        if job.platform == Platform.SIEMENS:
            return True
        return detect_platform(job.url) == Platform.SIEMENS

    def prepare(self, job: ApplicationJob) -> AdapterResult:
        """Prepare to apply: verify the Siemens repo is configured.

        This does NOT launch a browser. It only checks that the adapter
        is configured and the job has the required documents.
        """
        if not self._config.is_configured:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=Phase.PREPARE,
                message=(
                    "Siemens adapter is not configured: UAA_SIEMENS_REPO is not set "
                    "or the path does not exist"
                ),
                application_id=job.application_id,
                platform=self.platform,
                errors=["siemens_adapter_not_configured"],
            )

        # Check that the job has required documents for Siemens.
        if not job.cv_pdf:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=Phase.PREPARE,
                message="Job is missing cv_pdf, which Siemens requires",
                application_id=job.application_id,
                platform=self.platform,
                errors=["missing_cv_pdf"],
            )

        return AdapterResult.success(
            phase=Phase.PREPARE,
            message="Siemens adapter is configured and job has required documents",
            application_id=job.application_id,
            platform=self.platform,
            next_action="navigate_to_form",
        )

    def navigate_to_form(self, job: ApplicationJob) -> AdapterResult:
        """Navigate to the Siemens application form.

        For Phase 2, this invokes the Siemens CLI in dry-run mode and
        maps the result. The actual navigation is performed by the
        existing Siemens ``ApplyWorkflow``; this adapter only wraps it.
        """
        return self._invoke_siemens_cli(job, phase=Phase.NAVIGATE)

    def fill(self, job: ApplicationJob) -> AdapterResult:
        """Fill the Siemens application form.

        For Phase 2, this invokes the Siemens CLI in dry-run mode and
        maps the result.
        """
        return self._invoke_siemens_cli(job, phase=Phase.FILL)

    def submit_or_pause(self, job: ApplicationJob) -> AdapterResult:
        """Submit or pause for the Siemens application.

        Respects the adapter's ``dry_run`` config. If dry_run is True
        (the default), the Siemens CLI is invoked with ``--dry-run`` and
        the result is mapped to ``dry_run`` status. If dry_run is False,
        the CLI is invoked with ``--no-dry-run`` and the result is mapped
        to ``submitted`` or ``failed``.
        """
        return self._invoke_siemens_cli(job, phase=Phase.SUBMIT)

    def _invoke_siemens_cli(
        self,
        job: ApplicationJob,
        phase: Phase,
    ) -> AdapterResult:
        """Invoke the Siemens CLI and map the result to AdapterResult.

        This is the narrow boundary. It:
        1. Checks configuration.
        2. Builds the CLI command (``python main.py --job-id <id> [--dry-run|--no-dry-run] [--headless]``).
        3. Runs the subprocess with a timeout.
        4. Maps the exit code to an :class:`AdapterResult`.

        The adapter does NOT parse human-readable log lines. It relies on
        the subprocess exit code and (if available) structured output.

        Args:
            job: The job to apply to.
            phase: The current phase (navigate, fill, submit).

        Returns:
            A structured :class:`AdapterResult`.
        """
        if not self._config.is_configured:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=phase,
                message="Siemens adapter is not configured",
                application_id=job.application_id,
                platform=self.platform,
                errors=["siemens_adapter_not_configured"],
            )

        # Determine the Siemens job ID. Siemens uses numeric job IDs; we
        # extract it from the job's external_job_id or job_id field.
        siemens_job_id = job.external_job_id or job.job_id
        if not siemens_job_id:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=phase,
                message=(
                    "Cannot invoke Siemens CLI: job has no external_job_id or job_id. "
                    "Siemens requires a numeric job ID."
                ),
                application_id=job.application_id,
                platform=self.platform,
                errors=["missing_siemens_job_id"],
            )

        # Build the CLI command.
        # repo_path is guaranteed non-None here because is_configured checks
        # it above, but Pyright cannot propagate that through the property.
        repo_path = self._config.repo_path
        assert repo_path is not None  # for type narrowing
        main_py = repo_path / "siemens-auto-apply" / "main.py"
        if not main_py.exists():
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=phase,
                message=f"Siemens main.py not found at {main_py}",
                application_id=job.application_id,
                platform=self.platform,
                errors=["siemens_main_py_not_found"],
            )

        cmd: list[str] = [
            self._config.python_executable,
            str(main_py),
            "--job-id",
            str(siemens_job_id),
        ]

        if self._config.dry_run:
            cmd.append("--dry-run")
        else:
            cmd.append("--no-dry-run")

        if self._config.headless:
            cmd.append("--headless")

        logger.info(
            "[%s] siemens invoke: %s (phase=%s, dry_run=%s)",
            job.application_id[:12],
            " ".join(cmd),
            phase,
            self._config.dry_run,
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                cwd=str(repo_path / "siemens-auto-apply"),
            )
        except subprocess.TimeoutExpired:
            return AdapterResult.failed(
                phase=phase,
                message=f"Siemens CLI timed out after {self._config.timeout_seconds}s",
                application_id=job.application_id,
                platform=self.platform,
                errors=["timeout"],
            )
        except Exception as exc:
            return AdapterResult.failed(
                phase=phase,
                message=f"Siemens CLI invocation failed: {exc}",
                application_id=job.application_id,
                platform=self.platform,
                errors=[str(exc)],
            )

        # Map the exit code to an AdapterResult.
        # Siemens main.py exit codes:
        #   0 = success (at least one job succeeded or dry-run completed)
        #   1 = failure (all jobs failed)
        #   2 = config error
        #   130 = interrupted
        if result.returncode == 0:
            if self._config.dry_run:
                status = AdapterResultStatus.DRY_RUN
                message = "Siemens CLI completed in dry-run mode"
            else:
                status = AdapterResultStatus.SUBMITTED
                message = "Siemens CLI submitted the application"
            return AdapterResult(
                status=status,
                phase=phase,
                message=message,
                application_id=job.application_id,
                platform=self.platform,
                next_action="verify" if phase == Phase.SUBMIT else None,
            )
        elif result.returncode == 2:
            return AdapterResult(
                status=AdapterResultStatus.BLOCKED,
                phase=phase,
                message=f"Siemens CLI config error (exit 2): {result.stderr[:500]}",
                application_id=job.application_id,
                platform=self.platform,
                errors=["config_error"],
            )
        elif result.returncode == 130:
            return AdapterResult(
                status=AdapterResultStatus.FAILED,
                phase=phase,
                message="Siemens CLI was interrupted (exit 130)",
                application_id=job.application_id,
                platform=self.platform,
                errors=["interrupted"],
            )
        else:
            return AdapterResult.failed(
                phase=phase,
                message=f"Siemens CLI failed (exit {result.returncode}): {result.stderr[:500]}",
                application_id=job.application_id,
                platform=self.platform,
                errors=[f"exit_code_{result.returncode}"],
            )


__all__ = ["SiemensAdapter", "SiemensAdapterConfig"]
