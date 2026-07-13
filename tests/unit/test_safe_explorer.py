"""Tests for :mod:`universal_auto_applier.navigator.safe_explorer`.

Tests the safe exploration loop with mock observe/click callbacks.
No browser is launched.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.core.statuses import PageState
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import (
    DEFAULT_MAX_STEPS,
    safe_explore,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "forms"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestSafeExplorerReachesForm:
    def test_reaches_form_after_clicking_apply(self) -> None:
        """Simulates: apply page -> click Apply -> form page."""
        apply_html = _read_fixture("apply_button.html")
        form_html = _read_fixture("simple_application.html")

        pages = [apply_html, form_html]
        page_index = [0]

        def observe():
            html = pages[page_index[0]]
            return observe_html(html, url=f"https://example.com/step/{page_index[0]}")

        def click(selector: str) -> bool:
            # Advance to the next page after clicking.
            page_index[0] += 1
            return True

        result = safe_explore(observe, click, max_steps=5)

        assert result.reached_form
        assert result.final_state == PageState.FORM
        assert result.stopped_reason == "form_visible"
        assert result.step_count >= 2  # at least observe + click + observe

    def test_stops_on_captcha(self) -> None:
        captcha_html = _read_fixture("captcha_page.html")

        def observe():
            return observe_html(captcha_html, url="https://example.com/captcha")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.final_state == PageState.CAPTCHA
        assert result.stopped_reason == "captcha_detected"

    def test_stops_on_login(self) -> None:
        login_html = _read_fixture("login_page.html")

        def observe():
            return observe_html(login_html, url="https://example.com/login")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.final_state == PageState.LOGIN
        assert result.stopped_reason == "login_required"

    def test_stops_on_review(self) -> None:
        review_html = _read_fixture("review_submit.html")

        def observe():
            return observe_html(review_html, url="https://example.com/review")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.final_state == PageState.REVIEW
        assert result.stopped_reason == "review_page"

    def test_stops_on_unknown_page(self) -> None:
        unknown_html = "<html><body><h1>Hello</h1></body></html>"

        def observe():
            return observe_html(unknown_html, url="https://example.com/unknown")

        def click(selector: str) -> bool:
            return True

        result = safe_explore(observe, click)

        assert result.final_state == PageState.UNKNOWN
        assert result.stopped_reason == "no_safe_action"

    def test_stops_on_max_steps(self) -> None:
        """If we never reach a form or stop condition, max_steps applies."""
        # A page with a safe_continue button that never changes the page.
        continue_html = '<html><body><button id="next">Continue</button></body></html>'

        call_count = [0]

        def observe():
            call_count[0] += 1
            return observe_html(continue_html, url="https://example.com/loop")

        def click(selector: str) -> bool:
            # Don't advance — simulate a page that doesn't change.
            return True

        result = safe_explore(observe, click, max_steps=3)

        assert result.stopped_reason == "max_steps_reached"
        assert result.step_count <= 4  # 3 steps + 1 final observe

    def test_never_clicks_dangerous_submit(self) -> None:
        """The explorer must never click a dangerous_submit button."""
        # A page with only a submit button (no safe clickables).
        submit_html = '<html><body><button type="submit">Submit application</button></body></html>'

        clicked_selectors: list[str] = []

        def observe():
            return observe_html(submit_html, url="https://example.com/submit-only")

        def click(selector: str) -> bool:
            clicked_selectors.append(selector)
            return True

        result = safe_explore(observe, click)

        # The explorer should stop — either because it detected the submit
        # button (submit_detected) or because there are no safe clickables
        # (no_safe_action). Both are acceptable; the key assertion is that
        # no click was made.
        assert result.stopped_reason in ("submit_detected", "no_safe_action")
        # No click should have been made.
        assert len(clicked_selectors) == 0

    def test_click_failure_stops_exploration(self) -> None:
        apply_html = _read_fixture("apply_button.html")

        def observe():
            return observe_html(apply_html, url="https://example.com/apply")

        def click(selector: str) -> bool:
            return False  # click fails

        result = safe_explore(observe, click)

        assert result.stopped_reason == "click_failed"

    def test_logs_steps(self) -> None:
        """Every step should be recorded with URL and action."""
        apply_html = _read_fixture("apply_button.html")
        form_html = _read_fixture("simple_application.html")

        pages = [apply_html, form_html]
        page_index = [0]

        def observe():
            html = pages[page_index[0]]
            return observe_html(html, url=f"https://example.com/step/{page_index[0]}")

        def click(selector: str) -> bool:
            page_index[0] += 1
            return True

        result = safe_explore(observe, click, max_steps=5)

        for step in result.steps:
            assert step.url  # every step has a URL
            assert step.action  # every step has an action
            assert step.step_number > 0

    def test_default_max_steps(self) -> None:
        assert DEFAULT_MAX_STEPS == 10
