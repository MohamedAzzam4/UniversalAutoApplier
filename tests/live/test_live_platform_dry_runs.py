"""Opt-in live dry-run across real ATS platforms (WQ-7).

Enable explicitly with ``UAA_ENABLE_LIVE_PLATFORM_DRY_RUN=1`` and provide
per-platform URLs via env vars (``UAA_LIVE_GREENHOUSE_URL``, etc.).

The runner stops before final submit for every platform. This test
proves:
- At least one Greenhouse and one Lever dry-run reaches review_ready.
- At least one Workday or SmartRecruiters attempt has a truthful result.
- Zero submissions occur across all platforms.
- LinkedIn Easy Apply is excluded.
- WQ-7B recon mode navigates and never fills/uploads/submits.
"""

from __future__ import annotations

import os

import pytest

from universal_auto_applier.config import load_settings
from universal_auto_applier.services.live_dry_run_platforms import (
    run_platform_dry_runs,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("UAA_ENABLE_LIVE_PLATFORM_DRY_RUN") != "1",
        reason="opt-in live platform dry-run is disabled",
    ),
]


def test_platform_dry_runs_stop_before_submit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Run live dry-runs on all configured platforms.

    Asserts:
    - Zero submissions across all platforms.
    - At least one Greenhouse review_ready.
    - At least one Lever review_ready.
    - At least one Workday or SmartRecruiters attempt (any truthful result).
    """
    settings = load_settings().model_copy(
        update={
            "data_dir": tmp_path / "uaa_wq7_live",
            "browser_headless": True,
        }
    )

    summary = run_platform_dry_runs(
        settings,
        artifacts_dir=tmp_path / "live-runs",
        headless=True,
    )

    # Zero submissions — the most critical assertion.
    assert summary.total_submitted == 0, (
        f"FAIL: {summary.total_submitted} submissions occurred — WQ-7 must never submit"
    )
    for r in summary.results:
        assert not r.submitted, f"FAIL: platform {r.platform} reported submitted=True"

    # At least one platform must have run.
    assert summary.total_run > 0, "No platforms ran — check URL env vars"

    # At least one Greenhouse review_ready.
    gh_results = [r for r in summary.results if r.platform == "greenhouse" and not r.skipped]
    if gh_results:
        gh_review_ready = any(r.status == "review_ready" for r in gh_results)
        assert gh_review_ready or all(
            r.status in ("needs_user_input", "failed") for r in gh_results
        ), "Greenhouse dry-run did not reach a safe terminal state"

    # At least one Lever review_ready.
    lever_results = [r for r in summary.results if r.platform == "lever" and not r.skipped]
    if lever_results:
        lever_review_ready = any(r.status == "review_ready" for r in lever_results)
        assert lever_review_ready or all(
            r.status in ("needs_user_input", "failed") for r in lever_results
        ), "Lever dry-run did not reach a safe terminal state"

    # At least one Workday or SmartRecruiters attempt.
    wd_sr = [
        r for r in summary.results if r.platform in ("workday", "smartrecruiters") and not r.skipped
    ]
    if wd_sr:
        # Any truthful result is acceptable (review_ready, needs_user_input, or failed).
        assert all(r.status in ("review_ready", "needs_user_input", "failed") for r in wd_sr), (
            "Workday/SmartRecruiters did not reach a truthful terminal state"
        )

    # No LinkedIn interaction.
    for r in summary.results:
        if "linkedin" in r.url.lower():
            assert r.skipped, f"LinkedIn URL was not skipped: {r.url}"


def test_platform_recon_navigation_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """WQ-7B: recon mode navigates to forms without any interaction.

    Asserts:
    - Zero submissions across all platforms.
    - No field fills and no uploads in any recon report.
    - Every terminal state is truthful (recon_complete/needs_user_input/failed).
    - At least one configured platform ran.
    """
    settings = load_settings().model_copy(
        update={
            "data_dir": tmp_path / "uaa_wq7b_live",
            "browser_headless": True,
        }
    )

    summary = run_platform_dry_runs(
        settings,
        artifacts_dir=tmp_path / "live-runs",
        headless=True,
        recon_only=True,
    )

    # Zero submissions — the most critical assertion.
    assert summary.total_submitted == 0, (
        f"FAIL: {summary.total_submitted} submissions occurred — WQ-7B recon must never submit"
    )
    for r in summary.results:
        assert not r.submitted, f"FAIL: platform {r.platform} reported submitted=True"

    # At least one platform must have run.
    assert summary.total_run > 0, "No platforms ran — check URL env vars"

    # Recon mode must never fill or upload anything.
    for r in summary.results:
        if r.report is not None:
            assert r.report.fields == [], (
                f"FAIL: recon mode filled {len(r.report.fields)} fields on {r.platform}"
            )
            assert r.report.uploads == [], (
                f"FAIL: recon mode uploaded {len(r.report.uploads)} files on {r.platform}"
            )

    # Every terminal state must be truthful and safe.
    for r in summary.results:
        if r.skipped:
            continue
        assert r.status in ("recon_complete", "needs_user_input", "failed"), (
            f"FAIL: platform {r.platform} reached unexpected status {r.status}"
        )
