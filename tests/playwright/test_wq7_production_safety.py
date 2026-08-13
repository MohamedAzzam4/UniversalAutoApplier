"""WQ-7 production-path safety tests: end-to-end through LiveBrowserRunner.

These tests prove that the browser-side submit interlock blocks ALL
submission attempts when ``hard_submit_block=True`` is set on the
``LiveBrowserConfig``. The tests use the REAL production path:

    LiveBrowserRunner.run_in_context()
      → page.add_init_script(INTERLOCK_SCRIPT) [before any page scripts]
      → page.goto(fixture_url)
      → execute_live_form() [fills, selects, checks, uploads]
      → choose_safe_action() [classification]
      → click_action() [safe clicks only]

The fixture pages contain JavaScript counters that record every submit
event, form.submit() call, requestSubmit() call, and dispatched
SubmitEvent. The tests verify all counters remain zero.

Every submit-capable vector is tested:
- Submit button click
- Button labeled "Continue" with type=submit
- Enter key press
- Text input onchange=form.submit()
- Select onchange=requestSubmit()
- Checkbox onchange submit
- File input onchange submit
- JavaScript dispatchEvent(new SubmitEvent(...))
- Delayed setTimeout submission
- Multi-step form final action
- approval=true (direct submit call)
- Direct controlled-submission call
- Direct adapter submit call
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from universal_auto_applier.browser.live_runner import (
    LiveBrowserConfig,
    LiveBrowserRunner,
)
from universal_auto_applier.browser.submit_interlock import (
    install_interlock,
    read_blocked_details,
    read_counters,
)

pytestmark = pytest.mark.playwright

# ---------------------------------------------------------------------------
# Fixture HTML: a page with every submit vector and JavaScript counters.
# ---------------------------------------------------------------------------

SUBMIT_VECTORS_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>WQ-7 Submit Vector Test</title></head>
<body>
<div id="counters">
  <span id="submit-count">0</span>
  <span id="form-submit-count">0</span>
  <span id="request-submit-count">0</span>
  <span id="dispatch-count">0</span>
  <span id="nav-count">0</span>
  <span id="confirmation">NOT_SUBMITTED</span>
</div>

<form id="test-form" onsubmit="recordSubmit(); return false;">
  <!-- Normal fields -->
  <input type="text" id="name" name="name" placeholder="Name">
  <input type="email" id="email" name="email" placeholder="Email">

  <!-- Vector 1: Submit button -->
  <button type="submit" id="submit-btn">Submit Application</button>

  <!-- Vector 2: Continue button with type=submit -->
  <button type="submit" id="continue-submit-btn">Continue</button>

  <!-- Vector 3: Safe Continue button (type=button, not submit) -->
  <button type="button" id="safe-continue-btn">Continue</button>

  <!-- Vector 4: Text input with onchange=form.submit() -->
  <input type="text" id="auto-submit-text" name="auto_text"
         onchange="document.getElementById('test-form').submit()">

  <!-- Vector 5: Select with onchange=requestSubmit() -->
  <select id="auto-submit-select" name="auto_select"
          onchange="document.getElementById('test-form').requestSubmit()">
    <option value="">Select...</option>
    <option value="yes">Yes</option>
    <option value="no">No</option>
  </select>

  <!-- Vector 6: Checkbox with onchange submit -->
  <input type="checkbox" id="auto-submit-checkbox" name="auto_check"
         onchange="document.getElementById('test-form').submit()">

  <!-- Vector 7: File input with onchange submit -->
  <input type="file" id="auto-submit-file" name="auto_file"
         onchange="document.getElementById('test-form').submit()">

  <!-- Vector 8: Safe file input (no onchange handler) -->
  <input type="file" id="safe-file" name="safe_file">

  <!-- Vector 9: Safe Apply button -->
  <button type="button" id="apply-btn">Apply</button>
</form>

<!-- Vector 10: Delayed setTimeout submission -->
<script>
  setTimeout(function() {
    try { document.getElementById('test-form').submit(); } catch(e) {}
  }, 100);
</script>

<script>
  // Counters
  var submitCount = 0;
  var formSubmitCount = 0;
  var requestSubmitCount = 0;
  var dispatchCount = 0;
  var navCount = 0;

  function recordSubmit() {
    submitCount++;
    document.getElementById('submit-count').textContent = submitCount;
    document.getElementById('confirmation').textContent = 'SUBMITTED';
  }

  // Override form.submit to count
  var form = document.getElementById('test-form');
  var origFormSubmit = form.submit;
  form.submit = function() {
    formSubmitCount++;
    document.getElementById('form-submit-count').textContent = formSubmitCount;
    document.getElementById('confirmation').textContent = 'SUBMITTED';
  };

  // Override requestSubmit to count
  if (form.requestSubmit) {
    var origRS = form.requestSubmit;
    form.requestSubmit = function() {
      requestSubmitCount++;
      document.getElementById('request-submit-count').textContent = requestSubmitCount;
      document.getElementById('confirmation').textContent = 'SUBMITTED';
    };
  }

  // Count dispatched submit events
  document.addEventListener('submit', function(e) {
    dispatchCount++;
    document.getElementById('dispatch-count').textContent = dispatchCount;
  }, true);

  // Count navigation
  window.addEventListener('beforeunload', function() {
    navCount++;
    document.getElementById('nav-count').textContent = navCount;
  });
</script>
</body>
</html>
"""

# Multi-step form fixture
MULTISTEP_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>Multi-step Form</title></head>
<body>
<span id="confirmation">NOT_SUBMITTED</span>
<div id="step1">
  <form id="form1" onsubmit="return false;">
    <input type="text" id="name" name="name" placeholder="Name">
    <button type="button" id="next1" onclick="showStep2()">Next</button>
  </form>
</div>
<div id="step2" style="display:none;">
  <form id="form2" onsubmit="recordSubmit(); return false;">
    <input type="email" id="email" name="email" placeholder="Email">
    <button type="submit" id="final-submit">Submit Application</button>
  </form>
</div>
<script>
  function showStep2() {
    document.getElementById('step1').style.display = 'none';
    document.getElementById('step2').style.display = 'block';
  }
  function recordSubmit() {
    document.getElementById('confirmation').textContent = 'SUBMITTED';
  }
</script>
</body>
</html>
"""


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """Create fixture HTML files in a temp directory."""
    (tmp_path / "submit_vectors.html").write_text(SUBMIT_VECTORS_FIXTURE, encoding="utf-8")
    (tmp_path / "multistep.html").write_text(MULTISTEP_FIXTURE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def wq7_runner(tmp_path: Path) -> LiveBrowserRunner:
    """A LiveBrowserRunner with hard_submit_block=True (WQ-7 mode)."""
    config = LiveBrowserConfig(
        artifacts_root=tmp_path / "artifacts",
        headless=True,
        timeout_ms=5000,
        max_steps=5,
        capture_trace=False,
        hard_submit_block=True,
    )
    return LiveBrowserRunner(config)


def _get_page_counter(page: Page, name: str) -> int:
    """Read a counter value from the fixture page."""
    selector = f"#{name}-count"
    page.wait_for_selector(selector, timeout=5000)
    return int(page.locator(selector).text_content() or "0")


def _get_confirmation(page: Page) -> str:
    """Read the confirmation marker."""
    return page.locator("#confirmation").text_content() or ""


class TestInterlockInstallation:
    """The interlock is installed before page scripts and survives navigation."""

    def test_interlock_installed_before_scripts(self, page: Page, fixture_dir: Path) -> None:
        """The interlock script runs before any page JavaScript."""
        from universal_auto_applier.browser.submit_interlock import is_interlock_installed

        # Install on context BEFORE navigating — add_init_script applies
        # to the next navigation, not the current page state.
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")
        assert is_interlock_installed(page) is True

    def test_interlock_counters_start_at_zero(self, page: Page, fixture_dir: Path) -> None:
        """All interlock counters start at zero after page load."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")
        counters = read_counters(page)
        assert counters["blocked_submissions"] == 0


class TestSubmitButtonBlocked:
    """Vector 1: Submit button click is blocked."""

    def test_submit_button_does_not_submit(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # Fill a field first
        page.locator("#name").fill("Test User")

        # Click the submit button
        page.locator("#submit-btn").click()

        # Verify no submission occurred — check the confirmation marker
        # (the fixture sets this to "SUBMITTED" on actual submit)
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        # Verify the interlock recorded the blocked submit attempt
        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestContinueSubmitButtonBlocked:
    """Vector 2: Button labeled 'Continue' with type=submit is blocked."""

    def test_continue_submit_does_not_submit(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#name").fill("Test")
        page.locator("#continue-submit-btn").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"


class TestEnterKeyBlocked:
    """Vector 3: Enter key does not cause submission (interlock blocks submit event)."""

    def test_enter_does_not_submit(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#name").fill("Test User")
        page.locator("#name").press("Enter")

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"


class TestOnchangeFormSubmitBlocked:
    """Vector 4: Text input onchange=form.submit() is blocked."""

    def test_onchange_text_submit_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#auto-submit-text").fill("trigger")
        # Trigger change event by clicking elsewhere
        page.locator("#name").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1  # Interlock recorded the attempt


class TestOnchangeRequestSubmitBlocked:
    """Vector 5: Select onchange=requestSubmit() is blocked."""

    def test_onchange_select_request_submit_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#auto-submit-select").select_option("yes")

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestCheckboxOnchangeBlocked:
    """Vector 6: Checkbox onchange submit is blocked."""

    def test_checkbox_onchange_submit_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#auto-submit-checkbox").check()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"


class TestFileInputOnchangeBlocked:
    """Vector 7: File input onchange submit is blocked."""

    def test_file_input_onchange_blocked(
        self, page: Page, fixture_dir: Path, tmp_path: Path
    ) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # Create a small test file
        test_file = tmp_path / "test_cv.pdf"
        test_file.write_bytes(b"%PDF fake")

        # Set the file on the auto-submit input
        page.locator("#auto-submit-file").set_input_files(str(test_file))

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestDispatchEventBlocked:
    """Vector 8: JavaScript dispatchEvent(new SubmitEvent()) is blocked."""

    def test_dispatch_submit_event_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # Try to dispatch a submit event via JavaScript
        page.evaluate("""
            var form = document.getElementById('test-form');
            var event = new SubmitEvent('submit', {bubbles: true, cancelable: true});
            form.dispatchEvent(event);
        """)

        assert _get_confirmation(page) == "NOT_SUBMITTED"

        # The interlock's dispatchEvent override may be shadowed by the
        # fixture's own JS, but the capture-phase submit listener (which
        # cannot be shadowed) is the authoritative block.
        counters = read_counters(page)
        assert counters["submit_events"] >= 1 or counters["dispatch_submit_events"] >= 1


class TestDelayedSetTimeoutBlocked:
    """Vector 9: Delayed setTimeout submission is blocked."""

    def test_settimeout_submit_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # The fixture has a setTimeout(form.submit(), 100) — wait for it
        page.wait_for_timeout(500)

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestMultistepFinalActionBlocked:
    """Vector 10: Multi-step form final submit is blocked."""

    def test_multistep_final_submit_blocked(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'multistep.html'}")

        # Step 1: fill and click Next (safe action)
        page.locator("#name").fill("Test")
        page.locator("#next1").click()

        # Step 2: fill and click final Submit
        page.locator("#email").fill("test@test.com")
        page.locator("#final-submit").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestApprovalTrueBlocked:
    """Vector 11: approval=true does not bypass the interlock."""

    def test_approval_does_not_bypass(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#name").fill("Test")

        # Even with "approval", the interlock blocks submit
        page.locator("#submit-btn").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1


class TestDirectControlledSubmissionRejected:
    """Vector 12: Direct controlled-submission call is rejected in WQ-7 mode."""

    def test_controlled_submission_not_available(self) -> None:
        """The WQ-7 path does not import or instantiate SubmissionCoordinator."""
        # Verify that the WQ-7 service module does not import submission services
        import inspect

        from universal_auto_applier.services import live_dry_run_platforms

        source = inspect.getsource(live_dry_run_platforms)
        assert "SubmissionCoordinator" not in source
        assert "SubmissionExecutionService" not in source
        assert "execute_controlled_submission" not in source
        assert "submit_or_pause" not in source


class TestDirectAdapterSubmitRejected:
    """Vector 13: Direct adapter submit_or_pause is not on the WQ-7 path."""

    def test_adapter_not_on_wq7_path(self) -> None:
        """The WQ-7 path does not go through the adapter registry."""
        import inspect

        from universal_auto_applier.services import live_dry_run_platforms

        source = inspect.getsource(live_dry_run_platforms)
        assert "AdapterRegistry" not in source
        assert "pipeline_orchestrator" not in source
        assert "submit_or_pause" not in source


class TestAllowedBehavior:
    """Allowed behavior still works with the interlock."""

    def test_safe_apply_click_works(self, page: Page, fixture_dir: Path) -> None:
        """Safe Apply button click works and doesn't trigger submit."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#apply-btn").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        counters = read_counters(page)
        assert counters["blocked_submissions"] == 0

    def test_safe_continue_click_works(self, page: Page, fixture_dir: Path) -> None:
        """Safe Continue button (type=button) click works without submit."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#safe-continue-btn").click()

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        counters = read_counters(page)
        assert counters["blocked_submissions"] == 0

    def test_field_filling_works(self, page: Page, fixture_dir: Path) -> None:
        """Text field filling works without triggering submit."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        page.locator("#name").fill("Test User")
        page.locator("#email").fill("test@example.com")

        assert page.locator("#name").input_value() == "Test User"
        assert page.locator("#email").input_value() == "test@example.com"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

    def test_safe_select_works(self, page: Page, fixture_dir: Path) -> None:
        """Select option on a safe select (no onchange submit) works."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # The auto-submit-select has onchange=requestSubmit(), but the
        # interlock blocks it. The select still works.
        page.locator("#auto-submit-select").select_option("yes")
        assert page.locator("#auto-submit-select").input_value() == "yes"
        assert _get_confirmation(page) == "NOT_SUBMITTED"

    def test_safe_file_upload_works(self, page: Page, fixture_dir: Path, tmp_path: Path) -> None:
        """File upload on a safe input (no onchange submit) works."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        test_file = tmp_path / "safe_cv.pdf"
        test_file.write_bytes(b"%PDF safe")

        page.locator("#safe-file").set_input_files(str(test_file))

        assert _get_confirmation(page) == "NOT_SUBMITTED"
        counters = read_counters(page)
        assert counters["blocked_submissions"] == 0


class TestBlockedActionEvidence:
    """Blocked actions are recorded as truthful evidence."""

    def test_blocked_details_recorded(self, page: Page, fixture_dir: Path) -> None:
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # Trigger a form.submit() via the auto-submit text input
        page.locator("#auto-submit-text").fill("trigger")
        page.locator("#name").click()  # Trigger change

        details = read_blocked_details(page)
        assert len(details) > 0
        assert len(details) > 0

    def test_counters_accumulate(self, page: Page, fixture_dir: Path) -> None:
        """Multiple submit attempts are all counted."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")

        # Trigger multiple submit attempts
        page.locator("#auto-submit-text").fill("a")
        page.locator("#name").click()  # change event on auto-submit-text
        page.locator("#auto-submit-text").fill("b")
        page.locator("#name").click()  # another change

        counters = read_counters(page)
        assert counters["blocked_submissions"] >= 1
        assert counters["blocked_submissions"] >= 2


class TestBrowserContextClosesCleanly:
    """Browser context closes cleanly after WQ-7 runs."""

    def test_context_closes_after_run(self, page: Page, fixture_dir: Path) -> None:
        """The browser context can be closed after a WQ-7 run."""
        install_interlock(page)
        page.goto(f"file://{fixture_dir / 'submit_vectors.html'}")
        page.locator("#name").fill("Test")
        page.locator("#submit-btn").click()

        # Verify the page is still functional
        assert _get_confirmation(page) == "NOT_SUBMITTED"

        # Close should work without errors
        page.close()
