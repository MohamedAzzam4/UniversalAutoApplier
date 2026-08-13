"""WQ-7: Per-platform real ATS dry-run orchestration.

This module implements the opt-in live dry-run across multiple real ATS
platforms (Greenhouse, Lever, Workday, SmartRecruiters). It reuses the
existing ``LiveBrowserRunner`` — which is already platform-agnostic and
submit-safe — and adds a per-platform dispatch layer with evidence caching.

Safety:
- Never performs final submission. The runner's safety logic (no submit-
  clicking code, ``submitted=False`` forced in finally) is inherited.
- The WQ-7 hard submit block (``hard_submit_block=True``) provides an
  additional lowest-layer guarantee: even a direct call to ``attempt_submit``
  returns "blocked" without clicking.
- All network access is opt-in via ``UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=1``.
- No live tests run in default CI.
- LinkedIn Easy Apply is explicitly excluded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from universal_auto_applier.browser.live_models import LiveRunReport
from universal_auto_applier.browser.live_runner import (
    LiveBrowserConfig,
    LiveBrowserRunner,
)
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform

logger = logging.getLogger("universal_auto_applier.live_dry_run_platforms")


@dataclass
class PlatformDryRunResult:
    """The outcome of one platform's dry-run."""

    platform: str
    url: str
    report: LiveRunReport | None = None
    skipped: bool = False
    skip_reason: str = ""
    timestamp: str = ""

    @property
    def submitted(self) -> bool:
        """Always False — WQ-7 never submits."""
        if self.report is not None:
            return self.report.submitted
        return False

    @property
    def status(self) -> str:
        """The terminal status of this platform's run."""
        if self.skipped:
            return "skipped"
        if self.report is not None:
            return self.report.status
        return "not_run"

    @property
    def stopped_reason(self) -> str:
        """Why the run stopped."""
        if self.skipped:
            return self.skip_reason
        if self.report is not None:
            return self.report.stopped_reason
        return ""


@dataclass
class PlatformDryRunSummary:
    """Aggregate report across all platforms."""

    results: list[PlatformDryRunResult] = field(default_factory=list[PlatformDryRunResult])
    started_at: str = ""
    finished_at: str = ""
    total_platforms: int = 0
    total_run: int = 0
    total_skipped: int = 0
    total_review_ready: int = 0
    total_needs_user_input: int = 0
    total_failed: int = 0
    total_submitted: int = 0  # Must always be 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_platforms": self.total_platforms,
            "total_run": self.total_run,
            "total_skipped": self.total_skipped,
            "total_review_ready": self.total_review_ready,
            "total_needs_user_input": self.total_needs_user_input,
            "total_failed": self.total_failed,
            "total_submitted": self.total_submitted,
            "results": [
                {
                    "platform": r.platform,
                    "url": r.url,
                    "status": r.status,
                    "stopped_reason": r.stopped_reason,
                    "submitted": r.submitted,
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "timestamp": r.timestamp,
                    "report_path": r.report.report_path if r.report else None,
                    "final_url": r.report.final_url if r.report else "",
                    "fields_count": len(r.report.fields) if r.report else 0,
                    "uploads_count": len(r.report.uploads) if r.report else 0,
                    "clicks_count": len(r.report.click_path) if r.report else 0,
                    "errors": r.report.errors if r.report else [],
                }
                for r in self.results
            ],
        }


def _get_platform_urls(settings: Settings) -> dict[str, str]:
    """Build the platform→URL map from settings (env vars)."""
    urls: dict[str, str] = {}
    if settings.live_greenhouse_url:
        urls["greenhouse"] = settings.live_greenhouse_url
    if settings.live_lever_url:
        urls["lever"] = settings.live_lever_url
    if settings.live_workday_url:
        urls["workday"] = settings.live_workday_url
    if settings.live_smartrecruiters_url:
        urls["smartrecruiters"] = settings.live_smartrecruiters_url
    return urls


def _filter_platforms(urls: dict[str, str], platforms_filter: str | None) -> dict[str, str]:
    """Filter to the requested subset of platforms."""
    if not platforms_filter:
        return urls
    requested = {p.strip().lower() for p in platforms_filter.split(",")}
    return {k: v for k, v in urls.items() if k in requested}


def _make_synthetic_job(
    url: str, platform_name: str, artifacts_dir: Path | None = None
) -> ApplicationJob:
    """Create a synthetic ApplicationJob for a platform dry-run.

    Uses a deterministic application_id derived from the URL. The job uses
    the SyntheticProfile (no real candidate PII) and generates synthetic
    CV/cover letter PDFs marked as TEST DATA.
    """
    from universal_auto_applier.synthetic_profile import (
        SyntheticProfile,
        create_synthetic_documents,
    )

    application_id = compute_application_id(
        platform=platform_name, external_job_id=f"wq7-dry-run-{platform_name}", url=url
    )
    profile = SyntheticProfile()

    # Generate synthetic documents in the artifacts directory
    doc_dir = (artifacts_dir or Path("/tmp")) / "wq7-documents"
    cv_path, cover_path = create_synthetic_documents(doc_dir)

    return ApplicationJob(
        application_id=application_id,
        platform=Platform(platform_name)
        if platform_name in Platform.__members__.values()
        else Platform.GENERIC,
        source="wq7_live_dry_run",
        company=f"WQ-7 {platform_name.title()} Dry-Run",
        title=f"Real ATS Dry-Run ({platform_name.title()})",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_path),
        cover_letter_pdf=str(cover_path),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id=f"wq7-dry-run-{platform_name}",
        metadata=profile.to_metadata(),
    )


def run_platform_dry_runs(
    settings: Settings,
    *,
    artifacts_dir: Path | None = None,
    headless: bool | None = None,
    profile_dir: Path | None = None,
    max_steps: int | None = None,
    timeout_ms: int | None = None,
    qa_service: Any = None,
) -> PlatformDryRunSummary:
    """Run live dry-runs across configured platforms.

    Args:
        settings: The application settings (must have per-platform URLs
            configured via env vars).
        artifacts_dir: Override for the artifacts root directory.
        headless: Override for headless mode.
        profile_dir: Override for the browser profile directory.
        max_steps: Override for max navigation steps.
        timeout_ms: Override for the page timeout.
        qa_service: Optional LLM QA service for question resolution.

    Returns:
        A summary of all platform dry-run results.

    Raises:
        RuntimeError: If ``enable_live_platform_dry_run`` is False.
    """
    if not settings.enable_live_platform_dry_run:
        raise RuntimeError(
            "Live platform dry-run is not enabled. Set "
            "UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=true to opt in."
        )

    summary = PlatformDryRunSummary(
        started_at=datetime.now(UTC).isoformat(),
    )

    all_urls = _get_platform_urls(settings)
    if not all_urls:
        logger.warning(
            "[wq7] No platform URLs configured. Set UAA_LIVE_GREENHOUSE_URL, "
            "UAA_LIVE_LEVER_URL, UAA_LIVE_WORKDAY_URL, "
            "UAA_LIVE_SMARTRECRUITERS_URL to specify real ATS URLs."
        )
        summary.finished_at = datetime.now(UTC).isoformat()
        return summary

    urls = _filter_platforms(all_urls, settings.live_dry_run_platforms)
    summary.total_platforms = len(urls)

    # Determine artifacts root
    art_root = artifacts_dir or (settings.data_dir / "live-runs" / "wq7-platforms")
    art_root.mkdir(parents=True, exist_ok=True)

    # Determine browser config
    effective_headless = headless if headless is not None else settings.browser_headless
    effective_profile = profile_dir or settings.browser_profile_dir
    effective_max_steps = max_steps or settings.browser_max_steps
    effective_timeout = timeout_ms or settings.browser_timeout_ms

    for platform_name, url in urls.items():
        result: PlatformDryRunResult = PlatformDryRunResult(
            platform=platform_name,
            url=url,
            timestamp=datetime.now(UTC).isoformat(),
        )

        # Skip LinkedIn Easy Apply (safety rule)
        if "linkedin" in url.lower():
            result.skipped = True
            result.skip_reason = "LinkedIn Easy Apply is excluded from WQ-7"
            summary.results.append(result)
            summary.total_skipped += 1
            logger.info("[wq7] Skipping %s: LinkedIn excluded", platform_name)
            continue

        try:
            logger.info("[wq7] Starting dry-run for %s: %s", platform_name, url)

            job = _make_synthetic_job(url, platform_name, art_root)
            config = LiveBrowserConfig(
                artifacts_root=art_root / platform_name,
                profile_dir=effective_profile,
                headless=effective_headless,
                channel=settings.browser_channel,
                timeout_ms=effective_timeout,
                max_steps=effective_max_steps,
                capture_trace=True,
                hard_submit_block=True,  # WQ-7 hard submit block
            )
            runner = LiveBrowserRunner(config)
            report = runner.run(job, candidate=None, qa_service=qa_service)
            result.report = report

            if report.status == "review_ready":
                summary.total_review_ready += 1
            elif report.status == "needs_user_input":
                summary.total_needs_user_input += 1
            elif report.status == "failed":
                summary.total_failed += 1

            summary.total_run += 1
            logger.info(
                "[wq7] %s complete: status=%s stopped_reason=%s submitted=%s",
                platform_name,
                report.status,
                report.stopped_reason,
                report.submitted,
            )

        except Exception as exc:  # noqa: BLE001
            result.report = LiveRunReport(
                application_id="",
                started_at=datetime.now(UTC),
                initial_url=url,
                final_url="",
                status="failed",
                stopped_reason="platform_run_error",
                errors=[f"{type(exc).__name__}: {exc}"],
                submitted=False,
            )
            summary.total_failed += 1
            summary.total_run += 1
            logger.exception("[wq7] %s failed: %s", platform_name, exc)

        summary.results.append(result)

    summary.finished_at = datetime.now(UTC).isoformat()
    summary.total_submitted = sum(1 for r in summary.results if r.submitted)

    # Write summary
    summary_path = art_root / f"summary-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    import json

    summary_path.write_text(json.dumps(summary.to_dict(), indent=2), encoding="utf-8")
    logger.info(
        "[wq7] All platforms complete: %d run, %d skipped, %d review_ready, "
        "%d needs_user_input, %d failed, %d submitted (must be 0)",
        summary.total_run,
        summary.total_skipped,
        summary.total_review_ready,
        summary.total_needs_user_input,
        summary.total_failed,
        summary.total_submitted,
    )

    return summary


__all__ = [
    "PlatformDryRunResult",
    "PlatformDryRunSummary",
    "run_platform_dry_runs",
]
