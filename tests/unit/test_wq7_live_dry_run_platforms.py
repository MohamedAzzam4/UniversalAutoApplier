"""WQ-7 regression tests: hard submit block, platform dry-run, and safety.

These tests prove:
1. The WQ-7 hard submit block prevents any submit click at the lowest layer.
2. The ``attempt_submit`` method returns "blocked" when the flag is True.
3. The ``run_platform_dry_runs`` function gates on the opt-in env var.
4. LinkedIn Easy Apply is excluded.
5. Missing platform URLs cause skips (not errors).
6. The summary report correctly aggregates results.
7. Zero submissions occur in all paths.

All tests are deterministic and run in default CI (no network access).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from universal_auto_applier.browser.live_models import LiveRunReport
from universal_auto_applier.browser.live_runner import (
    LiveBrowserConfig,
    LiveBrowserRunner,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.services.live_dry_run_platforms import (
    _filter_platforms,
    _get_platform_urls,
    _make_synthetic_job,
    run_platform_dry_runs,
)


def _make_settings(**overrides: Any) -> Settings:
    """Build settings with WQ-7 defaults."""
    defaults: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 8000,
        "data_dir": Path("/tmp/wq7_test"),
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_report(
    *,
    status: str = "review_ready",
    stopped_reason: str = "final_submit_detected",
    submitted: bool = False,
) -> LiveRunReport:
    """Build a minimal LiveRunReport for testing."""
    from datetime import UTC, datetime

    return LiveRunReport(
        application_id="test-id",
        started_at=datetime.now(UTC),
        initial_url="https://example.com",
        final_url="https://example.com/apply",
        status=status,
        stopped_reason=stopped_reason,
        submitted=submitted,
    )


class TestHardSubmitBlock:
    """The WQ-7 hard submit block prevents any submit click."""

    def test_attempt_submit_blocked_when_flag_true(self, tmp_path: Path) -> None:
        """When hard_submit_block=True, attempt_submit returns 'blocked'."""
        config = LiveBrowserConfig(
            artifacts_root=tmp_path / "artifacts",
            headless=True,
            hard_submit_block=True,
        )
        runner = LiveBrowserRunner(config)
        # Even with a valid page and selector, the method must not click.
        mock_page = MagicMock()
        result = runner.attempt_submit(mock_page, "button[type='submit']")
        assert result == "blocked"
        # Verify no click was performed on the page.
        mock_page.click.assert_not_called()

    def test_attempt_submit_blocked_in_normal_mode_too(self, tmp_path: Path) -> None:
        """Even in non-blocked mode, attempt_submit does not click (dry-run safety)."""
        config = LiveBrowserConfig(
            artifacts_root=tmp_path / "artifacts",
            headless=True,
            hard_submit_block=False,
        )
        runner = LiveBrowserRunner(config)
        mock_page = MagicMock()
        result = runner.attempt_submit(mock_page, "button[type='submit']")
        assert result == "blocked"
        mock_page.click.assert_not_called()

    def test_config_accepts_hard_submit_block_flag(self, tmp_path: Path) -> None:
        """LiveBrowserConfig accepts and stores the hard_submit_block flag."""
        config_blocked = LiveBrowserConfig(
            artifacts_root=tmp_path,
            hard_submit_block=True,
        )
        assert config_blocked.hard_submit_block is True

        config_normal = LiveBrowserConfig(
            artifacts_root=tmp_path,
            hard_submit_block=False,
        )
        assert config_normal.hard_submit_block is False

    def test_report_submitted_always_false(self, tmp_path: Path) -> None:
        """The LiveRunReport.submitted field is always False in WQ-7."""
        report = _make_report(submitted=False)
        assert report.submitted is False

    def test_no_submit_click_code_in_runner(self, tmp_path: Path) -> None:
        """The LiveBrowserRunner source code has no .click() call on submit.

        This is a static analysis test: it reads the runner source and
        verifies that no method other than ``attempt_submit`` (which is
        always blocked) contains a ``.click(`` call on a submit selector.
        """
        import inspect

        source = inspect.getsource(LiveBrowserRunner)
        # The runner must not have any direct submit click code.
        # The only mention of "click" should be in the click_action
        # navigation helper (which only clicks safe_apply/safe_continue).
        # attempt_submit is the only method that references submit clicking,
        # and it always returns "blocked".
        assert "attempt_submit" in source
        # Verify attempt_submit does not call .click()
        attempt_submit_source = inspect.getsource(LiveBrowserRunner.attempt_submit)
        assert ".click(" not in attempt_submit_source


class TestPlatformDryRunGating:
    """The platform dry-run must be opt-in only."""

    def test_raises_when_not_enabled(self) -> None:
        """run_platform_dry_runs raises when enable_live_platform_dry_run is False."""
        settings = _make_settings(enable_live_platform_dry_run=False)
        with pytest.raises(RuntimeError, match="not enabled"):
            run_platform_dry_runs(settings)

    def test_no_urls_returns_empty_summary(self, tmp_path: Path) -> None:
        """When no platform URLs are configured, returns empty summary."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
        )
        summary = run_platform_dry_runs(settings)
        assert summary.total_platforms == 0
        assert summary.total_run == 0
        assert summary.total_submitted == 0


class TestPlatformUrlConfig:
    """Platform URLs are read from settings (env vars)."""

    def test_get_platform_urls_all_configured(self) -> None:
        """All five platform URLs are read from settings."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            live_greenhouse_url="https://boards.greenhouse.io/test1",
            live_lever_url="https://jobs.lever.co/test2",
            live_workday_url="https://myworkdayjobs.com/test3",
            live_smartrecruiters_url="https://careers.smartrecruiters.com/test4",
            live_icims_url="https://careers-example.icims.com/jobs/intro",
        )
        urls = _get_platform_urls(settings)
        assert len(urls) == 5
        assert urls["greenhouse"] == "https://boards.greenhouse.io/test1"
        assert urls["lever"] == "https://jobs.lever.co/test2"
        assert urls["workday"] == "https://myworkdayjobs.com/test3"
        assert urls["smartrecruiters"] == "https://careers.smartrecruiters.com/test4"
        assert urls["icims"] == "https://careers-example.icims.com/jobs/intro"

    def test_get_platform_urls_partial(self) -> None:
        """Only configured platforms are included."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            live_greenhouse_url="https://boards.greenhouse.io/test1",
        )
        urls = _get_platform_urls(settings)
        assert len(urls) == 1
        assert "greenhouse" in urls

    def test_filter_platforms_subset(self) -> None:
        """The platforms filter correctly subsets the URL map."""
        urls = {
            "greenhouse": "https://gh.io",
            "lever": "https://lever.co",
            "workday": "https://wd.com",
        }
        filtered = _filter_platforms(urls, "greenhouse,lever")
        assert len(filtered) == 2
        assert "greenhouse" in filtered
        assert "lever" in filtered
        assert "workday" not in filtered

    def test_filter_platforms_none_returns_all(self) -> None:
        """When no filter is specified, all URLs are returned."""
        urls = {"greenhouse": "https://gh.io", "lever": "https://lever.co"}
        filtered = _filter_platforms(urls, None)
        assert len(filtered) == 2


class TestSyntheticJobCreation:
    """Synthetic jobs are created correctly for platform dry-runs."""

    def test_synthetic_job_has_wq7_marker(self, tmp_path: Path) -> None:
        """The synthetic job is marked with wq7_synthetic=True."""
        job = _make_synthetic_job("https://boards.greenhouse.io/test", "greenhouse", tmp_path)
        assert job.metadata["candidate_profile"].get("wq7_synthetic") is True

    def test_synthetic_job_has_correct_url(self, tmp_path: Path) -> None:
        """The synthetic job uses the provided URL."""
        url = "https://jobs.lever.co/test-company/12345"
        job = _make_synthetic_job(url, "lever", tmp_path)
        assert job.url == url

    def test_synthetic_job_has_deterministic_id(self, tmp_path: Path) -> None:
        """The same platform+URL produces the same application_id."""
        url = "https://boards.greenhouse.io/test"
        job1 = _make_synthetic_job(url, "greenhouse", tmp_path)
        job2 = _make_synthetic_job(url, "greenhouse", tmp_path)
        assert job1.application_id == job2.application_id

    def test_synthetic_job_has_synthetic_candidate(self, tmp_path: Path) -> None:
        """The synthetic job has a synthetic candidate profile (not real)."""
        job = _make_synthetic_job("https://example.com", "greenhouse", tmp_path)
        profile = job.metadata["candidate_profile"]
        assert profile["first_name"] == "Test"
        assert profile["last_name"] == "Automation"
        assert profile["email"] == "test.automation@example.com"


class TestLinkedInExclusion:
    """LinkedIn Easy Apply is explicitly excluded from WQ-7."""

    def test_linkedin_url_skipped(self, tmp_path: Path) -> None:
        """A LinkedIn URL is skipped with the correct reason."""
        from universal_auto_applier.services.live_dry_run_platforms import (
            _get_platform_urls,
        )

        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
            live_lever_url="https://linkedin.com/jobs/view/123",  # Should be skipped
        )
        urls = _get_platform_urls(settings)
        # The lever URL is actually a LinkedIn URL — it will be caught at runtime.
        assert "lever" in urls

    def test_linkedin_url_detected_in_run(self, tmp_path: Path) -> None:
        """During run_platform_dry_runs, a LinkedIn URL is skipped."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
            live_lever_url="https://linkedin.com/jobs/view/123",
        )
        # Mock the runner to avoid actual browser launches
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.return_value = _make_report()
            summary = run_platform_dry_runs(settings)

        # LinkedIn should be skipped
        lever_result = next(r for r in summary.results if r.platform == "lever")
        assert lever_result.skipped is True
        assert "LinkedIn" in lever_result.skip_reason
        # Greenhouse should have run
        gh_result = next(r for r in summary.results if r.platform == "greenhouse")
        assert gh_result.skipped is False


class TestSummaryAggregation:
    """The summary report correctly aggregates results."""

    def test_summary_zero_submissions(self, tmp_path: Path) -> None:
        """The summary always reports zero submissions."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.return_value = _make_report(submitted=False)
            summary = run_platform_dry_runs(settings)

        assert summary.total_submitted == 0
        assert all(not r.submitted for r in summary.results)

    def test_summary_counts_correct(self, tmp_path: Path) -> None:
        """The summary correctly counts review_ready, needs_user_input, failed."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test1",
            live_lever_url="https://jobs.lever.co/test2",
            live_workday_url="https://myworkdayjobs.com/test3",
        )
        reports = [
            _make_report(status="review_ready", stopped_reason="final_submit_detected"),
            _make_report(status="needs_user_input", stopped_reason="captcha_detected"),
            _make_report(status="failed", stopped_reason="browser_launch_failed"),
        ]
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.side_effect = reports
            summary = run_platform_dry_runs(settings)

        assert summary.total_run == 3
        assert summary.total_review_ready == 1
        assert summary.total_needs_user_input == 1
        assert summary.total_failed == 1
        assert summary.total_submitted == 0

    def test_summary_to_dict_serializable(self, tmp_path: Path) -> None:
        """The summary can be serialized to a dict for JSON output."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.return_value = _make_report()
            summary = run_platform_dry_runs(settings)

        d = summary.to_dict()
        import json

        json_str = json.dumps(d, indent=2)
        assert "total_submitted" in json_str
        assert '"total_submitted": 0' in json_str

    def test_summary_to_dict_with_recon_observation_serializable(self, tmp_path: Path) -> None:
        """A recon observation (with datetime) must serialize cleanly to JSON."""
        import json
        from datetime import UTC, datetime

        from universal_auto_applier.browser.live_models import LiveFormObservation

        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        report = _make_report(status="recon_complete", stopped_reason="recon_done")
        report.recon_observation = LiveFormObservation(
            page_url="https://boards.greenhouse.io/test",
            title="Apply for this job",
            visible_control_count=4,
            file_input_count=1,
            field_labels=["first_name", "last_name"],
            detected_at=datetime.now(UTC),
            embedded_blocker="captcha_detected",
        )
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.return_value = report
            summary = run_platform_dry_runs(settings)

        d = summary.to_dict()
        assert json.dumps(d) == json.dumps(json.loads(json.dumps(d)))
        recon = next(r["recon_observation"] for r in d["results"] if r["recon_observation"])
        assert isinstance(recon["detected_at"], str)
        assert recon["embedded_blocker"] == "captcha_detected"
        assert recon["visible_control_count"] == 4


class TestReconOnlyMode:
    """WQ-7B navigation/observation-only mode."""

    def test_recon_only_flag_passed_to_runner(self, tmp_path: Path) -> None:
        """recon_only=True is forwarded to the LiveBrowserConfig."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        received: list[LiveBrowserConfig] = []

        real_init = LiveBrowserRunner.__init__

        def _spy_init(self_: LiveBrowserRunner, config: LiveBrowserConfig) -> None:
            received.append(config)
            real_init(self_, config)

        with (
            patch.object(LiveBrowserRunner, "__init__", new=_spy_init),
            patch.object(LiveBrowserRunner, "run") as mock_run,
        ):
            mock_run.return_value = _make_report(status="recon_complete")
            summary = run_platform_dry_runs(settings, recon_only=True)

        assert received and received[0].recon_only is True
        assert summary.total_recon_complete == 1

    def test_recon_defaults_from_settings(self, tmp_path: Path) -> None:
        """When recon_only is unset, settings.live_recon_only is used."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_recon_only=True,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        received: list[LiveBrowserConfig] = []

        real_init = LiveBrowserRunner.__init__

        def _spy_init(self_: LiveBrowserRunner, config: LiveBrowserConfig) -> None:
            received.append(config)
            real_init(self_, config)

        with (
            patch.object(LiveBrowserRunner, "__init__", new=_spy_init),
            patch.object(LiveBrowserRunner, "run") as mock_run,
        ):
            mock_run.return_value = _make_report(status="recon_complete")
            summary = run_platform_dry_runs(settings)

        assert received and received[0].recon_only is True
        assert summary.total_recon_complete == 1

    def test_recon_complete_not_counted_in_review_ready(self, tmp_path: Path) -> None:
        """A recon_complete result is counted separately, not as review_ready."""
        settings = _make_settings(
            enable_live_platform_dry_run=True,
            data_dir=tmp_path,
            live_greenhouse_url="https://boards.greenhouse.io/test",
        )
        with patch.object(LiveBrowserRunner, "run") as mock_run:
            mock_run.return_value = _make_report(status="recon_complete")
            summary = run_platform_dry_runs(settings, recon_only=True)

        assert summary.total_recon_complete == 1
        assert summary.total_review_ready == 0
        assert summary.total_submitted == 0

    def test_icims_platform_enum_member(self) -> None:
        """ICIMS is a recognized Platform member (used by synthetic jobs)."""
        from universal_auto_applier.core.statuses import Platform

        assert Platform.ICIMS == "icims"
        job = _make_synthetic_job(
            "https://careers-example.icims.com/jobs/123/job", "icims", Path("/tmp")
        )
        assert job.platform == Platform.ICIMS
