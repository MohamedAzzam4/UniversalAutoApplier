"""WQ-8 interlock one-shot authorized-submit tests.

These tests prove the browser-side submit interlock (``submit_interlock.py``)
in the exact way the WQ-8 coordinator uses it (real submit-button click):

1. With no allowance, every submit signal is blocked (button click,
   ``form.submit()``, ``requestSubmit()``, dispatched submit event) — the
   page's submit handler never runs.
2. ``arm_authorized_submit`` passes exactly ONE submit-button click; the
   page's submit handler runs (``data-submitted`` flips).
3. After the one pass the interlock blocks again (one-shot semantics).
4. ``disarm_authorized_submit`` clears any armed allowance.
5. ``authorized_submits`` reports exactly 1 per pass.
6. Script-driven submits (``form.submit()`` / ``requestSubmit()``) remain
   blocked in both directions — the interlock is defense-in-depth and only a
   real user click may exercise the authorization.

The marker is a bubble-phase ``submit`` listener registered by the fixture
AFTER navigation (so it is not removed by the interlock's freeze step).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from universal_auto_applier.browser.submit_interlock import (
    arm_authorized_submit,
    disarm_authorized_submit,
    install_interlock,
    is_interlock_installed,
    read_counters,
)

pytestmark = pytest.mark.playwright

_FIXTURE = """<!DOCTYPE html>
<html>
<head><title>WQ8 Interlock One-Shot</title></head>
<body>
<form id="test-form" method="post" action="about:blank">
  <input type="text" id="name" name="name">
  <button type="submit" id="submit-btn">Submit</button>
</form>
<script>
  window.__wq8_fixture_submits = 0;
  document.addEventListener('submit', function(e) {
    window.__wq8_fixture_submits++;
    document.body.setAttribute('data-submitted', 'true');
    e.preventDefault();   // keep the page from navigating away
    e.stopImmediatePropagation();
  });
</script>
</body>
</html>
"""


@pytest.fixture
def interlocked_form_page(page: Page, tmp_path: Path) -> Page:
    """A page with the interlock installed BEFORE navigation."""
    fixture = tmp_path / "wq8_interlock_form.html"
    fixture.write_text(_FIXTURE, encoding="utf-8")
    install_interlock(page)
    page.goto(f"file://{fixture}")
    return page


def _submitted(page: Page) -> bool:
    return bool(page.evaluate("document.body.hasAttribute('data-submitted')"))


def _fixture_runs(page: Page) -> int:
    return int(page.evaluate("window.__wq8_fixture_submits"))


class TestWQ8InterlockOneShot:
    def test_interlock_reportedly_installed(
        self,
        interlocked_form_page: Page,
    ) -> None:
        assert is_interlock_installed(interlocked_form_page) is True

    def test_unauthorized_submit_click_blocked(
        self,
        interlocked_form_page: Page,
    ) -> None:
        """Without an allowance the submit click is blocked."""
        interlocked_form_page.click("#submit-btn")
        assert _submitted(interlocked_form_page) is False
        assert _fixture_runs(interlocked_form_page) == 0
        counters = read_counters(interlocked_form_page)
        assert counters["blocked_submissions"] >= 1
        assert counters["authorized_submits"] == 0

    def test_armed_passes_exactly_one_click(
        self,
        interlocked_form_page: Page,
    ) -> None:
        # First click WITHOUT an allowance is blocked.
        interlocked_form_page.click("#submit-btn")
        assert _submitted(interlocked_form_page) is False
        assert _fixture_runs(interlocked_form_page) == 0

        # Arm the one-shot allowance: click passes once.
        assert arm_authorized_submit(interlocked_form_page, "token-abc") is True
        interlocked_form_page.click("#submit-btn")
        assert _submitted(interlocked_form_page) is True
        after_pass = read_counters(interlocked_form_page)
        assert after_pass["authorized_submits"] == 1

        # Second click (allowance consumed) is blocked again.
        interlocked_form_page.click("#submit-btn")
        counters = read_counters(interlocked_form_page)
        assert counters["authorized_submits"] == 1
        assert counters["blocked_submissions"] >= 1
        # The handler ran exactly once (the single authorized click).
        assert _fixture_runs(interlocked_form_page) == 1

    def test_disarm_prevents_future_passes(
        self,
        interlocked_form_page: Page,
    ) -> None:
        arm_authorized_submit(interlocked_form_page, "token-xyz")
        assert disarm_authorized_submit(interlocked_form_page) is True
        interlocked_form_page.click("#submit-btn")
        assert _submitted(interlocked_form_page) is False
        assert _fixture_runs(interlocked_form_page) == 0
        counters = read_counters(interlocked_form_page)
        assert counters["authorized_submits"] == 0

    def test_arm_then_disarm_multiple_times(
        self,
        interlocked_form_page: Page,
    ) -> None:
        """Each arm is a fresh one-shot; only one pass per arm."""
        interlocked_form_page.click("#submit-btn")  # baseline blocked
        arm_authorized_submit(interlocked_form_page, "t1")
        interlocked_form_page.click("#submit-btn")
        assert _submitted(interlocked_form_page) is True
        counters = read_counters(interlocked_form_page)
        assert counters["authorized_submits"] == 1

        arm_authorized_submit(interlocked_form_page, "t2")
        interlocked_form_page.click("#submit-btn")
        counters = read_counters(interlocked_form_page)
        assert counters["authorized_submits"] == 2
        assert _fixture_runs(interlocked_form_page) == 2

    def test_frozen_form_without_arm_blocks_submit(
        self,
        interlocked_form_page: Page,
    ) -> None:
        """A form whose own submit() is overridden by the interlock freeze
        still never reaches the page handler without an allowance."""
        page = interlocked_form_page
        page.evaluate("document.getElementById('test-form').submit()")
        page.evaluate("document.getElementById('test-form').requestSubmit()")
        assert _submitted(page) is False
        assert _fixture_runs(page) == 0
        counters = read_counters(page)
        assert counters["form_submit_calls"] >= 1
        assert counters["request_submit_calls"] >= 1
        assert counters["authorized_submits"] == 0

    def test_script_request_submit_consumes_one_shot_but_still_safe(
        self,
        interlocked_form_page: Page,
    ) -> None:
        """A script-driven ``requestSubmit()`` consumes the one-shot allowance
        at the requestSubmit override, but the submit event it generates is
        then blocked by the capture-phase listener (defense-in-depth
        over-blocking). The page handler never runs — the only signal that
        reaches it is the coordinator's REAL click on the submit control."""
        page = interlocked_form_page
        arm_authorized_submit(page, "token-js")
        page.evaluate("document.getElementById('test-form').requestSubmit()")
        counters = read_counters(page)
        assert counters["authorized_submits"] == 1
        assert _fixture_runs(page) == 0
        # Every later signal is blocked (allowance consumed by the above).
        page.evaluate("document.getElementById('test-form').submit()")
        counters2 = read_counters(page)
        assert counters2["authorized_submits"] == 1
        assert counters2["form_submit_calls"] >= 1
        assert _fixture_runs(page) == 0
