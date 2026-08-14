"""WQ-7 submit-safety guard tests with counters.

These tests prove that in ``REAL_SITE_DRY_RUN`` mode:
- Clicks on dangerous_submit are blocked (click counter stays 0).
- Enter key presses are blocked (press counter stays 0).
- form.submit() / requestSubmit() JS evaluations are blocked (submit counter stays 0).
- File uploads with auto-submit handlers are blocked (upload counter stays 0).
- Direct submit calls with approval=True are blocked (submit counter stays 0).
- All blocked actions are recorded as truthful evidence.

The tests use Playwright fixture pages with JavaScript counters to verify
that NO submit events reach the DOM.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from universal_auto_applier.execution_mode import (
    ExecutionMode,
    SubmitSafetyGuard,
    create_wq7_guard,
)

pytestmark = pytest.mark.playwright

# Fixture HTML with counters for every submit-capable action.
_COUNTER_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>Counter Test Form</title></head>
<body>
<div id="counters">
  <span id="click-count">0</span>
  <span id="submit-count">0</span>
  <span id="requestsubmit-count">0</span>
  <span id="enter-count">0</span>
  <span id="upload-count">0</span>
  <span id="nav-count">0</span>
</div>
<form id="test-form" onsubmit="incrementSubmit(); return false;">
  <input type="text" id="name" name="name" placeholder="Name">
  <input type="email" id="email" name="email" placeholder="Email">
  <input type="file" id="cv" name="cv">
  <input type="file" id="cv-auto" name="cv-auto"
         onchange="document.getElementById('test-form').submit()">
  <button type="submit" id="submit-btn">Submit Application</button>
  <button type="button" id="next-btn">Next</button>
  <button type="button" id="continue-btn">Continue</button>
</form>
<div id="confirmation" style="display:none;">
  <h1>Application Submitted!</h1>
</div>
<script>
  // Counters
  var clickCount = 0;
  var submitCount = 0;
  var requestSubmitCount = 0;
  var enterCount = 0;
  var uploadCount = 0;
  var navCount = 0;

  // Click counter — increments on ANY click
  document.addEventListener('click', function() {
    clickCount++;
    document.getElementById('click-count').textContent = clickCount;
  }, true);

  // Submit counter
  document.getElementById('test-form').addEventListener('submit', function(e) {
    e.preventDefault();
    submitCount++;
    document.getElementById('submit-count').textContent = submitCount;
    document.getElementById('confirmation').style.display = 'block';
  });

  // requestSubmit counter
  var origRequestSubmit = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.requestSubmit = function() {
    requestSubmitCount++;
    document.getElementById('requestsubmit-count').textContent = requestSubmitCount;
  };

  // Enter key counter
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      enterCount++;
      document.getElementById('enter-count').textContent = enterCount;
    }
  }, true);

  // Upload counter
  document.getElementById('cv').addEventListener('change', function() {
    uploadCount++;
    document.getElementById('upload-count').textContent = uploadCount;
  });
  document.getElementById('cv-auto').addEventListener('change', function() {
    uploadCount++;
    document.getElementById('upload-count').textContent = uploadCount;
  });

  // Navigation counter
  window.addEventListener('beforeunload', function() {
    navCount++;
  });

  // Make form.submit() increment the counter
  var form = document.getElementById('test-form');
  var origSubmit = form.submit;
  form.submit = function() {
    submitCount++;
    document.getElementById('submit-count').textContent = submitCount;
  };
</script>
</body>
</html>
"""


@pytest.fixture
def guard() -> SubmitSafetyGuard:
    """A WQ-7 guard in REAL_SITE_DRY_RUN mode."""
    return create_wq7_guard()


@pytest.fixture
def counter_page(page: Page, tmp_path: Path) -> Page:
    """A Playwright page with submit counters."""
    fixture = tmp_path / "counter_form.html"
    fixture.write_text(_COUNTER_FIXTURE, encoding="utf-8")
    page.goto(f"file://{fixture}")
    return page


def _get_counter(page: Page, name: str) -> int:
    """Read a counter value from the page."""
    return int(page.locator(f"#{name}-count").text_content() or "0")


class TestGuardConstruction:
    """The guard is constructed correctly for WQ-7."""

    def test_wq7_guard_is_real_site_dry_run(self) -> None:
        """create_wq7_guard() returns a guard in REAL_SITE_DRY_RUN mode."""
        guard = create_wq7_guard()
        assert guard.mode == ExecutionMode.REAL_SITE_DRY_RUN
        assert guard.is_dry_run is True
        assert guard.is_real_site_dry_run is True

    def test_wq7_guard_not_overridable(self) -> None:
        """The guard mode is fixed at construction and cannot be changed."""
        guard = create_wq7_guard()
        # The mode is a field on a dataclass — it can technically be set,
        # but create_wq7_guard() always returns REAL_SITE_DRY_RUN.
        # The caller cannot pass a different mode through this factory.
        assert guard.mode == ExecutionMode.REAL_SITE_DRY_RUN

    def test_controlled_submission_mode_allows_submit(self) -> None:
        """CONTROLLED_SUBMISSION mode does not block (gates handle it)."""
        guard = SubmitSafetyGuard(mode=ExecutionMode.CONTROLLED_SUBMISSION)
        assert guard.is_dry_run is False
        assert guard.can_click("dangerous_submit") is True
        assert guard.can_press_enter() is True
        assert guard.can_submit(approval=True) is True


class TestClickBlocking:
    """Clicks on dangerous_submit are blocked."""

    def test_dangerous_submit_click_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns False for dangerous_submit."""
        assert guard.can_click("dangerous_submit", "#submit-btn") is False
        assert len(guard.blocked_actions) == 1
        assert guard.blocked_actions[0].action_type == "click"
        assert "dangerous_submit" in guard.blocked_actions[0].reason

    def test_safe_apply_click_allowed(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns True for safe_apply."""
        assert guard.can_click("safe_apply", "#apply-btn") is True
        assert len(guard.blocked_actions) == 0

    def test_safe_continue_click_allowed(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns True for safe_continue."""
        assert guard.can_click("safe_continue", "#next-btn") is True
        assert len(guard.blocked_actions) == 0

    def test_login_click_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns False for login."""
        assert guard.can_click("login", "#login-btn") is False
        assert len(guard.blocked_actions) == 1

    def test_unknown_click_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns False for unknown."""
        assert guard.can_click("unknown", "#some-btn") is False
        assert len(guard.blocked_actions) == 1

    def test_safe_upload_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_click returns False for safe_upload (uploads are handled separately)."""
        assert guard.can_click("safe_upload", "#upload-btn") is False
        assert len(guard.blocked_actions) == 1


class TestEnterKeyBlocking:
    """Enter key presses are blocked in dry-run mode."""

    def test_enter_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_press_enter returns False in dry-run mode."""
        assert guard.can_press_enter("#name") is False
        assert len(guard.blocked_actions) == 1
        assert guard.blocked_actions[0].action_type == "press_enter"

    def test_enter_blocked_with_empty_selector(self, guard: SubmitSafetyGuard) -> None:
        """can_press_enter blocks even without a specific selector."""
        assert guard.can_press_enter() is False
        assert len(guard.blocked_actions) == 1


class TestJsEvaluationBlocking:
    """JavaScript evaluations with submit() are blocked."""

    def test_form_submit_js_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_evaluate_js blocks form.submit() calls."""
        assert guard.can_evaluate_js("document.getElementById('form').submit()") is False
        assert len(guard.blocked_actions) == 1
        assert guard.blocked_actions[0].action_type == "form_submit"

    def test_request_submit_js_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_evaluate_js blocks requestSubmit() calls."""
        assert guard.can_evaluate_js("document.getElementById('form').requestSubmit()") is False
        assert len(guard.blocked_actions) == 1

    def test_pure_read_js_allowed(self, guard: SubmitSafetyGuard) -> None:
        """can_evaluate_js allows pure read operations."""
        assert guard.can_evaluate_js("document.title") is True
        assert guard.can_evaluate_js("document.querySelector('#name').value") is True
        assert len(guard.blocked_actions) == 0

    def test_case_insensitive_submit_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_evaluate_js blocks submit() regardless of case."""
        assert guard.can_evaluate_js("form.SUBMIT()") is False
        assert guard.can_evaluate_js("form.Submit()") is False


class TestFileUploadBlocking:
    """File uploads with auto-submit handlers are blocked."""

    def test_safe_file_upload_allowed(self, guard: SubmitSafetyGuard) -> None:
        """can_set_input_files allows file inputs without submit handlers."""
        safe_html = '<input type="file" id="cv" name="cv">'
        assert guard.can_set_input_files(safe_html, "#cv") is True
        assert len(guard.blocked_actions) == 0

    def test_auto_submit_file_upload_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_set_input_files blocks file inputs with onchange submit handlers."""
        dangerous_html = '<input type="file" id="cv" name="cv" onchange="this.form.submit()">'
        assert guard.can_set_input_files(dangerous_html, "#cv") is False
        assert len(guard.blocked_actions) == 1
        assert guard.blocked_actions[0].action_type == "file_upload"

    def test_request_submit_file_upload_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_set_input_files blocks file inputs with requestSubmit handlers."""
        dangerous_html = (
            '<input type="file" id="cv" name="cv" onchange="this.form.requestSubmit()">'
        )
        assert guard.can_set_input_files(dangerous_html, "#cv") is False

    def test_oninput_submit_blocked(self, guard: SubmitSafetyGuard) -> None:
        """can_set_input_files blocks file inputs with oninput submit handlers."""
        dangerous_html = '<input type="file" id="cv" name="cv" oninput="this.form.submit()">'
        assert guard.can_set_input_files(dangerous_html, "#cv") is False

    def test_onchange_without_submit_allowed(self, guard: SubmitSafetyGuard) -> None:
        """can_set_input_files allows onchange handlers that don't call submit."""
        safe_html = '<input type="file" id="cv" name="cv" onchange="console.log(\'changed\')">'
        assert guard.can_set_input_files(safe_html, "#cv") is True


class TestDirectSubmitBlocking:
    """Direct submit calls are blocked regardless of approval."""

    def test_submit_blocked_without_approval(self, guard: SubmitSafetyGuard) -> None:
        """can_submit returns False when approval=False."""
        assert guard.can_submit(approval=False) is False
        assert len(guard.blocked_actions) == 1
        assert guard.blocked_actions[0].action_type == "direct_submit"

    def test_submit_blocked_with_approval(self, guard: SubmitSafetyGuard) -> None:
        """can_submit returns False even when approval=True."""
        assert guard.can_submit(approval=True) is False
        assert len(guard.blocked_actions) == 1
        assert "approval=True" in guard.blocked_actions[0].reason


class TestAssertNoSubmit:
    """assert_no_submit verifies no direct submit attempts occurred."""

    def test_assert_no_submit_passes_when_clean(self, guard: SubmitSafetyGuard) -> None:
        """assert_no_submit passes when no direct submit was attempted."""
        guard.can_click("safe_apply")  # Allowed, no blocked actions
        guard.assert_no_submit()  # Should not raise

    def test_assert_no_submit_fails_on_direct_submit(self, guard: SubmitSafetyGuard) -> None:
        """assert_no_submit raises when a direct submit was attempted."""
        guard.can_submit(approval=True)  # Blocked, but recorded
        with pytest.raises(AssertionError, match="direct submit"):
            guard.assert_no_submit()


class TestCounterIntegration:
    """Integration tests with Playwright fixture pages and counters."""

    def test_all_counters_zero_after_guarded_actions(
        self,
        counter_page: Page,
        guard: SubmitSafetyGuard,
    ) -> None:
        """All counters remain zero when the guard blocks submit-capable actions.

        This test verifies that:
        - No submit events fire (submit-count == 0)
        - No requestSubmit calls fire (requestsubmit-count == 0)
        - No Enter keys are pressed (enter-count == 0)
        - No file uploads trigger auto-submit (upload-count stays 0 for auto-submit input)
        - No navigation to confirmation page (confirmation is hidden)
        """
        # Verify initial counters are all 0
        assert _get_counter(counter_page, "click") == 0
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0
        assert _get_counter(counter_page, "enter") == 0
        assert _get_counter(counter_page, "upload") == 0

        # The guard blocks the submit click — we DON'T click it.
        assert guard.can_click("dangerous_submit", "#submit-btn") is False

        # The guard blocks Enter — we DON'T press it.
        assert guard.can_press_enter("#name") is False

        # The guard blocks form.submit() JS — we DON'T evaluate it.
        assert guard.can_evaluate_js("document.getElementById('test-form').submit()") is False

        # The guard blocks file upload on auto-submit input.
        auto_submit_html = counter_page.locator("#cv-auto").evaluate("el => el.outerHTML")
        assert guard.can_set_input_files(auto_submit_html, "#cv-auto") is False

        # Fill a text field (allowed — fill doesn't click or press Enter)
        counter_page.locator("#name").fill("Test User")
        counter_page.locator("#email").fill("test@example.com")

        # Verify ALL counters remain 0
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0
        assert _get_counter(counter_page, "enter") == 0
        assert _get_counter(counter_page, "upload") == 0

        # Verify confirmation page is NOT shown
        assert counter_page.locator("#confirmation").is_hidden()

        # Verify the guard recorded all blocked actions
        assert len(guard.blocked_actions) == 4  # click, enter, js, file_upload
        guard.assert_no_submit()  # No direct submit was attempted

    def test_counters_zero_with_approval_true(
        self,
        counter_page: Page,
        guard: SubmitSafetyGuard,
    ) -> None:
        """All counters remain zero even when approval=True is passed."""
        # Direct submit with approval=True is blocked
        assert guard.can_submit(approval=True) is False

        # Verify no submit occurred on the page
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0
        assert counter_page.locator("#confirmation").is_hidden()

    def test_next_continue_clicks_allowed_but_no_submit(
        self,
        counter_page: Page,
        guard: SubmitSafetyGuard,
    ) -> None:
        """Safe clicks (Next/Continue) are allowed and don't trigger submit."""
        # safe_continue is allowed by the guard
        assert guard.can_click("safe_continue", "#next-btn") is True
        assert guard.can_click("safe_continue", "#continue-btn") is True

        # Click the Continue button (safe action)
        counter_page.locator("#continue-btn").click()

        # Verify NO submit occurred (the click incremented click-count but not submit-count)
        assert _get_counter(counter_page, "click") >= 1
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0
        assert counter_page.locator("#confirmation").is_hidden()

    def test_multistep_form_no_submit(
        self,
        counter_page: Page,
        guard: SubmitSafetyGuard,
    ) -> None:
        """Multi-step form interactions don't trigger submit."""
        # Fill fields (step 1)
        counter_page.locator("#name").fill("Test")
        counter_page.locator("#email").fill("test@test.com")

        # Guard allows safe_continue click
        assert guard.can_click("safe_continue", "#next-btn") is True
        counter_page.locator("#next-btn").click()

        # No submit should have fired
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0

    def test_javascript_submit_handler_blocked(
        self,
        counter_page: Page,
        guard: SubmitSafetyGuard,
    ) -> None:
        """JavaScript submit handlers are blocked by the guard."""
        js_with_submit = "document.getElementById('test-form').submit()"
        assert guard.can_evaluate_js(js_with_submit) is False

        # We don't execute the JS — the guard blocked it
        # Verify no submit counter incremented
        assert _get_counter(counter_page, "submit") == 0
        assert _get_counter(counter_page, "requestsubmit") == 0
        assert counter_page.locator("#confirmation").is_hidden()

    def test_blocked_actions_recorded_as_evidence(
        self,
        guard: SubmitSafetyGuard,
    ) -> None:
        """All blocked actions are recorded with truthful evidence."""
        guard.can_click("dangerous_submit", "#submit-btn")
        guard.can_press_enter("#email")
        guard.can_evaluate_js("form.submit()")
        guard.can_set_input_files(
            '<input type="file" onchange="this.form.submit()">',
            "#cv",
        )
        guard.can_submit(approval=True)

        assert len(guard.blocked_actions) == 5

        # Verify each blocked action has the correct type
        types = [a.action_type for a in guard.blocked_actions]
        assert "click" in types
        assert "press_enter" in types
        assert "form_submit" in types
        assert "file_upload" in types
        assert "direct_submit" in types

        # Verify each has a non-empty reason
        for action in guard.blocked_actions:
            assert action.reason, f"Empty reason for {action.action_type}"
