"""Tests for :mod:`universal_auto_applier.adapters.siemens_adapter`.

Covers config validation, not_configured handling, and boundary behavior
using mock subprocess calls. No real Siemens CLI is invoked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from universal_auto_applier.adapters.siemens_adapter import (
    SiemensAdapter,
    SiemensAdapterConfig,
)
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import (
    AdapterResultStatus,
    ApplicationStatus,
    Phase,
    Platform,
)


def _make_siemens_job(
    *,
    url: str = "https://jobs.siemens.com/jobs/123",
    external_job_id: str = "510485",
    cv_pdf: str | None = "/tmp/cv.pdf",
    cover_letter_pdf: str | None = "/tmp/cover.pdf",
    status: ApplicationStatus | None = None,
) -> ApplicationJob:
    # If documents are missing, use EVALUATED instead of READY_TO_APPLY
    # (READY_TO_APPLY requires documents per the ApplicationJob contract).
    if status is None:
        if cv_pdf is None or cover_letter_pdf is None:
            status = ApplicationStatus.EVALUATED
        else:
            status = ApplicationStatus.READY_TO_APPLY

    application_id = compute_application_id(
        platform="siemens", external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.SIEMENS,
        source="siemens",
        company="Siemens",
        title="Working Student AI",
        url=url,
        location="Munich, Germany",
        job_description="Full JD",
        score=4.1,
        verdict="apply",
        cv_pdf=cv_pdf,
        cover_letter_pdf=cover_letter_pdf,
        status=status,
        external_job_id=external_job_id,
    )


class TestSiemensAdapterConfig:
    def test_defaults(self) -> None:
        config = SiemensAdapterConfig()
        assert config.repo_path is None
        assert config.dry_run is True
        assert config.headless is True
        assert config.timeout_seconds == 600

    def test_is_configured_false_when_repo_path_none(self) -> None:
        config = SiemensAdapterConfig(repo_path=None)
        assert not config.is_configured

    def test_is_configured_false_when_repo_path_does_not_exist(self, tmp_path: Path) -> None:
        config = SiemensAdapterConfig(repo_path=tmp_path / "nonexistent")
        assert not config.is_configured

    def test_is_configured_true_when_repo_path_exists(self, tmp_path: Path) -> None:
        config = SiemensAdapterConfig(repo_path=tmp_path)
        assert config.is_configured


class TestSiemensAdapterNotConfigured:
    def test_prepare_returns_blocked_when_not_configured(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_siemens_job()
        result = adapter.prepare(job)

        assert result.status == AdapterResultStatus.BLOCKED
        assert result.phase == Phase.PREPARE
        assert "not configured" in result.message
        assert "siemens_adapter_not_configured" in result.errors

    def test_navigate_returns_blocked_when_not_configured(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_siemens_job()
        result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.BLOCKED

    def test_fill_returns_blocked_when_not_configured(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_siemens_job()
        result = adapter.fill(job)

        assert result.status == AdapterResultStatus.BLOCKED

    def test_submit_returns_blocked_when_not_configured(self) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig())
        job = _make_siemens_job()
        result = adapter.submit_or_pause(job)

        assert result.status == AdapterResultStatus.BLOCKED


class TestSiemensAdapterPrepare:
    def test_prepare_succeeds_when_configured_and_documents_present(self, tmp_path: Path) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=tmp_path))
        job = _make_siemens_job(cv_pdf="/tmp/cv.pdf", cover_letter_pdf="/tmp/cover.pdf")
        result = adapter.prepare(job)

        assert result.status == AdapterResultStatus.SUCCESS
        assert result.phase == Phase.PREPARE
        assert result.next_action == "navigate_to_form"

    def test_prepare_blocked_when_cv_pdf_missing(self, tmp_path: Path) -> None:
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=tmp_path))
        job = _make_siemens_job(cv_pdf=None, cover_letter_pdf="/tmp/cover.pdf")
        result = adapter.prepare(job)

        assert result.status == AdapterResultStatus.BLOCKED
        assert "missing_cv_pdf" in result.errors


class TestSiemensAdapterBoundary:
    """Tests that the adapter invokes the Siemens CLI correctly without
    actually running it. Uses unittest.mock to patch subprocess.run.
    """

    def test_navigate_invokes_cli_with_dry_run(self, tmp_path: Path) -> None:
        # Create a fake Siemens repo structure.
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        main_py = siemens_subdir / "main.py"
        main_py.write_text("# fake")

        adapter = SiemensAdapter(
            SiemensAdapterConfig(repo_path=siemens_repo, dry_run=True, headless=True)
        )
        job = _make_siemens_job()

        # Mock subprocess.run to return exit code 0.
        mock_result = subprocess.CompletedProcess(
            args=["python", "main.py"],
            returncode=0,
            stdout="OK",
            stderr="",
        )
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            result = adapter.navigate_to_form(job)

        # Verify the CLI was called.
        assert mock_run.called
        call_args = mock_run.call_args
        cmd = call_args[0][0]  # first positional arg is the cmd list
        assert "--job-id" in cmd
        assert "510485" in cmd
        assert "--dry-run" in cmd
        assert "--headless" in cmd

        # Verify the result.
        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.phase == Phase.NAVIGATE
        assert result.application_id == job.application_id

    def test_submit_invokes_cli_with_no_dry_run(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(
            SiemensAdapterConfig(repo_path=siemens_repo, dry_run=False, headless=True)
        )
        job = _make_siemens_job()

        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            result = adapter.submit_or_pause(job)

        cmd = mock_run.call_args[0][0]
        assert "--no-dry-run" in cmd
        assert result.status == AdapterResultStatus.SUBMITTED

    def test_cli_failure_maps_to_failed_result(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))
        job = _make_siemens_job()

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="some error"
        )
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.FAILED
        assert "exit 1" in result.message
        assert "exit_code_1" in result.errors

    def test_cli_config_error_maps_to_blocked(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))
        job = _make_siemens_job()

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="config error"
        )
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.BLOCKED
        assert "config_error" in result.errors

    def test_cli_timeout_maps_to_failed(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo, timeout_seconds=1))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1),
        ):
            result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.FAILED
        assert "timed out" in result.message
        assert "timeout" in result.errors

    def test_missing_main_py_returns_blocked(self, tmp_path: Path) -> None:
        # repo_path exists but main.py is missing.
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_repo.mkdir()

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))
        job = _make_siemens_job()

        result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.BLOCKED
        assert "main.py not found" in result.message
        assert "siemens_main_py_not_found" in result.errors

    def test_missing_job_id_returns_blocked(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))

        # Create a job with no external_job_id and no job_id.
        url = "https://jobs.siemens.com/jobs/123"
        job_no_id = ApplicationJob(
            application_id=compute_application_id(platform=None, external_job_id=None, url=url),
            platform=Platform.SIEMENS,
            source="siemens",
            company="Siemens",
            title="Test",
            url=url,
            score=4.1,
            verdict="apply",
            cv_pdf="/tmp/cv.pdf",
            cover_letter_pdf="/tmp/cover.pdf",
            status=ApplicationStatus.READY_TO_APPLY,
        )

        result = adapter.navigate_to_form(job_no_id)
        assert result.status == AdapterResultStatus.BLOCKED
        assert "missing_siemens_job_id" in result.errors


class TestDryRunSafety:
    """Safety tests proving that navigate_to_form() and fill() can NEVER
    pass --no-dry-run to the Siemens CLI, regardless of config.dry_run.

    Only submit_or_pause() may pass --no-dry-run, and only when
    config.dry_run is False.
    """

    def _make_fake_repo(self, tmp_path: Path) -> Path:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")
        return siemens_repo

    def _mock_success(self) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def test_navigate_never_uses_no_dry_run_when_config_dry_run_false(self, tmp_path: Path) -> None:
        """navigate_to_form() must NOT pass --no-dry-run even when
        config.dry_run=False."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=False))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            adapter.navigate_to_form(job)

        cmd = mock_run.call_args[0][0]
        assert "--no-dry-run" not in cmd, (
            "navigate_to_form() must never pass --no-dry-run (it could submit)"
        )
        assert "--dry-run" in cmd, "navigate_to_form() must always pass --dry-run"

    def test_fill_never_uses_no_dry_run_when_config_dry_run_false(self, tmp_path: Path) -> None:
        """fill() must NOT pass --no-dry-run even when config.dry_run=False."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=False))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            adapter.fill(job)

        cmd = mock_run.call_args[0][0]
        assert "--no-dry-run" not in cmd, "fill() must never pass --no-dry-run (it could submit)"
        assert "--dry-run" in cmd, "fill() must always pass --dry-run"

    def test_submit_uses_dry_run_when_config_dry_run_true(self, tmp_path: Path) -> None:
        """submit_or_pause() passes --dry-run when config.dry_run=True."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=True))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            result = adapter.submit_or_pause(job)

        cmd = mock_run.call_args[0][0]
        assert "--dry-run" in cmd
        assert "--no-dry-run" not in cmd
        assert result.status == AdapterResultStatus.DRY_RUN

    def test_submit_uses_no_dry_run_when_config_dry_run_false(self, tmp_path: Path) -> None:
        """submit_or_pause() passes --no-dry-run when config.dry_run=False."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=False))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ) as mock_run:
            result = adapter.submit_or_pause(job)

        cmd = mock_run.call_args[0][0]
        assert "--no-dry-run" in cmd
        assert "--dry-run" not in cmd
        assert result.status == AdapterResultStatus.SUBMITTED

    def test_navigate_result_is_dry_run_even_when_config_dry_run_false(
        self, tmp_path: Path
    ) -> None:
        """navigate_to_form() result must be DRY_RUN, never SUBMITTED,
        even when config.dry_run=False."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=False))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ):
            result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.status != AdapterResultStatus.SUBMITTED

    def test_fill_result_is_dry_run_even_when_config_dry_run_false(self, tmp_path: Path) -> None:
        """fill() result must be DRY_RUN, never SUBMITTED, even when
        config.dry_run=False."""
        repo = self._make_fake_repo(tmp_path)
        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=repo, dry_run=False))
        job = _make_siemens_job()

        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=self._mock_success(),
        ):
            result = adapter.fill(job)

        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.status != AdapterResultStatus.SUBMITTED


class TestAdapterResultMapping:
    """Contract tests for AdapterResult success/failure mapping."""

    def test_success_result_has_correct_fields(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))
        job = _make_siemens_job()

        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.navigate_to_form(job)

        assert result.status == AdapterResultStatus.DRY_RUN
        assert result.phase == Phase.NAVIGATE
        assert result.application_id == job.application_id
        assert result.platform == Platform.SIEMENS
        assert isinstance(result.message, str)
        assert isinstance(result.errors, list)
        assert isinstance(result.screenshots, list)

    def test_failed_result_has_errors_list(self, tmp_path: Path) -> None:
        siemens_repo = tmp_path / "SiemensAutoApplier"
        siemens_subdir = siemens_repo / "siemens-auto-apply"
        siemens_subdir.mkdir(parents=True)
        (siemens_subdir / "main.py").write_text("# fake")

        adapter = SiemensAdapter(SiemensAdapterConfig(repo_path=siemens_repo))
        job = _make_siemens_job()

        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="automation error"
        )
        with patch(
            "universal_auto_applier.adapters.siemens_adapter.subprocess.run",
            return_value=mock_result,
        ):
            result = adapter.fill(job)

        assert result.status == AdapterResultStatus.FAILED
        assert len(result.errors) > 0
        assert isinstance(result.errors[0], str)
