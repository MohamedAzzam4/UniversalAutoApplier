"""WQ-8 intro/form heuristic — intro with single file input is not a form."""

import pytest
from playwright.sync_api import sync_playwright

from universal_auto_applier.navigator.apply_path_finder import analyze_page


@pytest.mark.playwright
def test_intro_like_page_is_not_form() -> None:
    """INTRO-like page: file_inputs=1, visible_controls=1, no signals → not a form."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        # Minimal intro-like HTML: one file input (Lebenslauf hochladen) and no other controls
        html = """
        <html><body>
          <form><input type="file" id="cv" name="cv" /></form>
          <a href="/de/jobs/411/form">Bewerbungsformular ausfüllen</a>
        </body></html>
        """
        page.set_content(html)
        page.wait_for_timeout(500)
        analysis = analyze_page(page)
        # With our fix, file_inputs=1 but visible_controls=1 → not a form
        # visible_controls counts file inputs as visible, but we require >=2 for file_inputs>0 case
        assert analysis.file_input_count == 1
        # visible_controls should be 1 (the file input)
        # is_application_form must be False for intro-like
        assert analysis.is_application_form is False
        browser.close()


@pytest.mark.playwright
def test_real_form_like_page_is_form() -> None:
    """REAL FORM-like page: multiple controls, signals, file inputs → is a form."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        html = """
        <html><body>
          <form>
            <input type="text" name="vorname" placeholder="Vorname" value="" />
            <input type="text" name="nachname" placeholder="Nachname" />
            <input type="email" name="email" placeholder="Email" />
            <input type="tel" name="phone" placeholder="Telefon" />
            <input type="text" name="city" placeholder="Ort" />
            <select name="country"><option>Deutschland</option></select>
            <input type="file" name="cv" accept=".pdf" />
            <input type="file" name="transcript" accept=".pdf" />
            <button type="submit">Absenden</button>
          </form>
        </body></html>
        """
        page.set_content(html)
        page.wait_for_timeout(500)
        analysis = analyze_page(page)
        assert analysis.visible_control_count >= 2
        assert analysis.file_input_count >= 1
        assert analysis.is_application_form is True
        assert analysis.has_dangerous_submit is True
        browser.close()


def test_heuristic_pure_logic_intro_not_form() -> None:
    """Pure logic: intro-like counts must not be a form."""
    file_inputs = 1
    visible_controls = 1
    application_signals = 0
    visible_forms = 1
    has_continue = False
    has_submit = False
    application_url_signal = False
    is_form = (
        (file_inputs > 0 and visible_controls >= 2)
        or (visible_controls >= 2 and application_signals >= 1)
        or (
            visible_forms > 0
            and visible_controls >= 1
            and application_signals >= 1
            and (has_continue or has_submit)
        )
        or (visible_forms > 0 and visible_controls >= 1 and application_url_signal)
    )
    assert is_form is False


def test_heuristic_pure_logic_form_is_form() -> None:
    """Pure logic: real form counts must be a form."""
    file_inputs = 2
    visible_controls = 30
    application_signals = 5
    visible_forms = 1
    has_continue = False
    has_submit = True
    application_url_signal = False
    is_form = (
        (file_inputs > 0 and visible_controls >= 2)
        or (visible_controls >= 2 and application_signals >= 1)
        or (
            visible_forms > 0
            and visible_controls >= 1
            and application_signals >= 1
            and (has_continue or has_submit)
        )
        or (visible_forms > 0 and visible_controls >= 1 and application_url_signal)
    )
    assert is_form is True
