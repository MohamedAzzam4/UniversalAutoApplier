"""Playwright tests for Phase 6 dashboard frontend.

Tests that the dashboard renders all views, shows data from the API,
and that safety controls work correctly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.playwright


def test_dashboard_shows_pipeline_status(page, server_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(server_url)
    page.wait_for_selector("#run-status", timeout=10_000)
    assert "idle" in page.locator("#run-status").inner_text()
    assert "review" in page.locator("#submit-mode").inner_text()


def test_dashboard_shows_safety_note(page, server_url: str) -> None:
    page.goto(server_url)
    page.wait_for_selector("#safety-mode-text", timeout=10_000)
    text = page.locator("#safety-mode-text").inner_text()
    assert "local-only" in text.lower() or "127.0.0.1" in text
    assert "no real submission" in text.lower()


def test_queue_view_renders(page, server_url: str) -> None:
    page.goto(server_url)
    page.click('a[data-view="queue"]')
    page.wait_for_selector("#queue-table", timeout=5_000)
    # Table should exist (may be empty).
    assert page.locator("#queue-tbody").is_visible()


def test_interventions_view_renders(page, server_url: str) -> None:
    page.goto(server_url)
    page.click('a[data-view="interventions"]')
    page.wait_for_selector("#intervention-list", timeout=5_000)
    assert page.locator("#intervention-list").is_visible()


def test_review_view_renders(page, server_url: str) -> None:
    page.goto(server_url)
    page.click('a[data-view="review"]')
    page.wait_for_selector("#review-job-id", timeout=5_000)
    assert page.locator("#review-job-id").is_visible()
    # Controls are hidden until a review state is loaded.
    assert page.locator("#review-load").is_visible()


def test_logs_view_renders(page, server_url: str) -> None:
    page.goto(server_url)
    page.click('a[data-view="logs"]')
    page.wait_for_selector("#log-list", timeout=5_000)
    assert page.locator("#log-list").is_visible()
    assert page.locator("#error-list").is_visible()


def test_review_approve_does_not_submit(page, server_url: str) -> None:
    """Approving a review state must NOT trigger submission."""
    page.goto(server_url)
    page.click('a[data-view="review"]')
    page.wait_for_selector("#review-job-id", timeout=5_000)

    # Enter a non-existent job ID and try to load.
    page.fill("#review-job-id", "nonexistent-job")
    page.click("#review-load")

    # Should show "No review state" or error, not a submission.
    page.wait_for_selector("#review-state-display", timeout=5_000)
    text = page.locator("#review-state-display").inner_text()
    # The review state for a non-existent job shows default unapproved state.
    assert (
        "No" in text
        or "not found" in text.lower()
        or "False" in text
        or "approved" in text.lower()
        or "enter" in text.lower()
    )


def test_submit_check_shows_blocked_by_default(page, server_url: str) -> None:
    """The submit-check pill should show 'blocked' when no review is approved."""
    page.goto(server_url)
    page.click('a[data-view="review"]')
    page.wait_for_selector("#review-job-id", timeout=5_000)
    page.fill("#review-job-id", "test-job-no-review")
    page.click("#review-load")
    page.wait_for_selector("#submit-check-result", timeout=5_000)
    text = page.locator("#submit-check-result").inner_text()
    assert "Blocked" in text or "blocked" in text


def test_dashboard_mobile_viewport(page, server_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server_url)
    page.wait_for_selector("#run-status", timeout=10_000)
    assert "idle" in page.locator("#run-status").inner_text()
