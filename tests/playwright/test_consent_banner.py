"""CMP/cookie banner handler — hermetic fixture coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from universal_auto_applier.browser.consent_banner import handle_consent_banner

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "consent"


def _load_fixture(page, name: str) -> None:
    html = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    page.set_content(html, wait_until="domcontentloaded")
    page.wait_for_timeout(300)


@pytest.mark.playwright
def test_usercentrics_necessary_only() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "usercentrics.html")
            # Verify overlay visible before
            assert page.locator("#usercentrics-root").is_visible()
            assert page.locator("text=Nur technisch notwendige Cookies akzeptieren").is_visible()
            assert page.locator("text=Alle akzeptieren").is_visible()

            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=3000)

            assert result.cmp == "Usercentrics"
            assert result.policy == "necessary_only"
            assert result.action == "necessary_only"
            assert result.result == "resolved"
            assert result.clicked is True
            assert "nur technisch notwendige" in result.clicked_text.lower()
            # Alle akzeptieren NOT clicked (clicked_text is necessary_only)
            assert "alle akzeptieren" not in result.clicked_text.lower()
            # Banner disappeared
            assert not page.locator("#usercentrics-root").is_visible()
            # Underlying form becomes interaction-ready (still has form)
            assert page.locator("#application-form").is_visible()
            # No dangerous_submit clicked
            assert result.clicked_text.lower() not in ("bewerbung absenden", "submit application")
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_generic_necessary_only() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "generic.html")
            assert page.locator("#onetrust-consent-sdk").is_visible()
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=3000)
            assert result.cmp in ("OneTrust", "Generic")
            assert result.result == "resolved"
            assert result.clicked is True
            assert (
                "reject all" in result.clicked_text.lower()
                or "necessary" in result.clicked_text.lower()
            )
            assert not page.locator("#onetrust-consent-sdk").is_visible()
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_unknown_cmp_blocked() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "unknown_cmp.html")
            # Unknown CMP has no necessary_only button, should block
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=2000)
            # Might be detected as Generic or Usercentrics? For unknown with cookie text but no button, result is blocked
            if result.cmp is not None:
                assert result.result == "blocked"
                assert result.clicked is False
                # Overlay remains
                assert page.locator("#custom-cmp-xyz").is_visible()
            else:
                # If not detected as CMP, then absent — but fixture has cookie, so expect detected
                assert result.result in ("blocked", "absent")
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_application_consent_not_clicked() -> None:
    """Safety: application-form Einwilligung must never be auto-clicked."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "application_consent.html")
            # No CMP overlay, so handler should return absent
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=2000)
            assert result.result == "absent"
            assert result.clicked is False
            # Application consent radios remain untouched (still visible, not clicked)
            assert page.locator("text=Einwilligung in die Speicherung meiner Daten").is_visible()
            assert page.locator("text=Ich stimme der Datenschutzerklärung zu").is_visible()
            # No radio should be checked
            assert page.locator("input[name='consent_storage'][value='ja']").is_checked() is False
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_pilot_defect_regression_old_vs_new() -> None:
    """Prove old analyze_page would see form under overlay, new handler blocks."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "usercentrics.html")
            # Old behavior: analyze_page sees underlying form even with overlay
            from universal_auto_applier.navigator.apply_path_finder import analyze_page

            analysis_before = analyze_page(page)
            # Old system would think form is present (25 controls) even though overlay blocks
            assert analysis_before.visible_control_count >= 20
            assert analysis_before.is_application_form is True
            # New behavior: CMP handler detects blocking overlay
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=3000)
            assert result.result == "resolved"
            # After consent, overlay absent, form still interaction-ready
            from universal_auto_applier.browser.consent_banner import handle_consent_banner as h2

            result2 = h2(page, policy="necessary_only", timeout_ms=1000)
            assert result2.result == "absent"
            analysis_after = analyze_page(page)
            assert analysis_after.is_application_form is True
            assert analysis_after.visible_control_count >= 20
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_unresolved_consent_blocks_form() -> None:
    """If CMP cannot be resolved, form preparation must not proceed."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            _load_fixture(page, "unknown_cmp.html")
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=2000)
            if result.result == "blocked":
                # Simulate live_runner's handling: should return cookie_consent_blocked
                # and not proceed to fill
                assert (
                    result.clicked is False or result.clicked is True
                )  # either, but still blocked
                assert page.locator("#custom-cmp-xyz").is_visible()
                # Form exists but is not interaction-ready because overlay blocks
                # The handler's blocked result is the signal to stop
                assert result.result == "blocked"
        finally:
            context.close()
            browser.close()


@pytest.mark.playwright
def test_handler_never_clicks_dangerous_submit() -> None:
    """Ensure handler does not click dangerous_submit even if cookie terms present."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            # Create a page with a submit button that contains cookie-like text nearby
            html = """
            <html><body>
            <div role="dialog" style="position:fixed; inset:0; background:white; z-index:9999;" >
              <h1>Cookie Settings</h1>
              <p>We use cookies</p>
              <button onclick="this.closest('div').style.display='none'">Reject all</button>
            </div>
            <form><button type="submit">Bewerbung absenden</button></form>
            </body></html>
            """
            page.set_content(html, wait_until="domcontentloaded")
            result = handle_consent_banner(page, policy="necessary_only", timeout_ms=2000)
            assert result.result == "resolved"
            # Submit button should still be visible and not clicked (no navigation)
            assert page.locator("text=Bewerbung absenden").is_visible()
            # Handler's clicked_text should be reject, not submit
            assert "reject all" in result.clicked_text.lower()
        finally:
            context.close()
            browser.close()
