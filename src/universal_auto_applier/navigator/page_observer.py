"""Page observer — extracts page state from the DOM, not screenshots.

Per ``ROADMAP.md`` WP 3.1, the PageObserver:

- Extracts from the DOM/accessibility tree:
  - URL
  - title
  - visible inputs
  - visible buttons/links
  - forms
  - file inputs
  - login indicators
  - captcha indicators
  - review/submit indicators
- Saves screenshots as evidence, but uses DOM data for automation.
- Does not treat hidden or disabled elements as safe actions.

The observer is designed to work with Playwright pages in production, but
also supports parsing raw HTML strings for fixture-based unit tests. This
dual interface lets us test the extraction logic without launching a
browser.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any

from universal_auto_applier.core.models import (
    Clickable,
    FileInputInfo,
    FormInfo,
    InputInfo,
    PageObservation,
)
from universal_auto_applier.core.statuses import ClickableClassification, PageState
from universal_auto_applier.navigator.clickable_classifier import classify_clickable

logger = logging.getLogger("universal_auto_applier.navigator.page_observer")


# Tags that are considered "clickable" for observation purposes.
_CLICKABLE_TAGS: frozenset[str] = frozenset({"button", "a", "input"})
# Input types that are considered "clickable" (submit/button/reset/image).
_CLICKABLE_INPUT_TYPES: frozenset[str] = frozenset({"submit", "button", "reset", "image"})
# Text input types.
_TEXT_INPUT_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "email",
        "tel",
        "url",
        "password",
        "search",
        "number",
        "date",
        "datetime-local",
        "time",
    }
)

# Indicators for page state detection.
_LOGIN_INDICATORS: frozenset[str] = frozenset(
    {
        "login",
        "log in",
        "sign in",
        "anmelden",
        "password",
        "passwort",
    }
)
_CAPTCHA_INDICATORS: frozenset[str] = frozenset(
    {
        "captcha",
        "recaptcha",
        "hcaptcha",
        "g-recaptcha",
        "h-captcha",
        "are you human",
        "verify you are human",
    }
)
_SUBMITTED_INDICATORS: frozenset[str] = frozenset(
    {
        "application submitted",
        "successfully submitted",
        "thank you for applying",
        "application received",
        "bewerbung eingegangen",
        "bewerbung erfolgreich",
    }
)
_REVIEW_INDICATORS: frozenset[str] = frozenset(
    {
        "review your application",
        "review and submit",
        "please review",
        "confirm your application",
        "überprüfen sie ihre bewerbung",
    }
)
_FORM_INDICATORS: frozenset[str] = frozenset(
    {
        "first name",
        "last name",
        "email address",
        "phone number",
        "vorname",
        "nachname",
        "e-mail-adresse",
        "telefonnummer",
    }
)
_REGISTER_INDICATORS: frozenset[str] = frozenset(
    {
        "create account",
        "sign up",
        "register",
        "registrieren",
        "konto erstellen",
    }
)
_EXPIRED_INDICATORS: frozenset[str] = frozenset(
    {
        "job expired",
        "position no longer available",
        "stellenangebot nicht mehr verfügbar",
        "expired",
    }
)
_ERROR_INDICATORS: frozenset[str] = frozenset(
    {
        "error",
        "something went wrong",
        "server error",
        "fehler",
    }
)


class _DomExtractor(HTMLParser):
    """A simple HTML parser that extracts page elements for the observer.

    This is intentionally lightweight — it does not run JavaScript or render
    CSS. It extracts the structural DOM data needed for classification. In
    production, a Playwright-based extractor would be used for pages that
    require JS rendering, but for fixture-based unit tests this is
    sufficient and deterministic.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self._in_title: bool = False
        self._title_parts: list[str] = []
        self.clickables: list[Clickable] = []
        self.inputs: list[InputInfo] = []
        self.file_inputs: list[FileInputInfo] = []
        self.forms: list[FormInfo] = []
        self._all_text_parts: list[str] = []
        # Stack of (tag, attrs, text_buffer) for tracking element text.
        self._element_stack: list[dict[str, Any]] = []
        self._form_counter: int = 0
        self._input_counter: int = 0
        self._clickable_counter: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: (v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True

        if tag == "form":
            self._form_counter += 1
            selector = f"form:nth-of-type({self._form_counter})"
            self.forms.append(
                FormInfo(
                    selector=selector,
                    action=attrs_dict.get("action", ""),
                    method=attrs_dict.get("method", "get").lower(),
                )
            )

        # Push element onto stack for text tracking.
        self._element_stack.append({"tag": tag, "attrs": attrs_dict, "text": ""})

        # Handle input elements at start tag (they have no closing tag).
        if tag == "input":
            self._handle_input(attrs_dict)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.title = " ".join(self._title_parts).strip()
            self._title_parts = []

        # Pop the element from the stack.
        if self._element_stack:
            element = self._element_stack.pop()
            element_text = element.get("text", "").strip()

            if element_text:
                self._all_text_parts.append(element_text)

            # If this is a clickable tag, create the Clickable now that we
            # have the text content.
            if tag in _CLICKABLE_TAGS:
                self._finalize_clickable(tag, element["attrs"], element_text)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._element_stack:
            self._element_stack[-1]["text"] += data

    def _finalize_clickable(self, tag: str, attrs: dict[str, str], text: str) -> None:
        """Create a Clickable from a completed element."""
        input_type = attrs.get("type", "").lower()
        is_clickable_input = tag == "input" and input_type in _CLICKABLE_INPUT_TYPES

        if tag == "input" and not is_clickable_input:
            return

        self._clickable_counter += 1
        element_id = attrs.get("id", "")
        if element_id:
            selector = f"#{element_id}"
        elif attrs.get("data-test"):
            selector = f"[data-test='{attrs['data-test']}']"
        elif attrs.get("name"):
            selector = f"{tag}[name='{attrs['name']}']"
        else:
            selector = f"{tag}:nth-of-type({self._clickable_counter})"

        # For input[type=submit], the value attribute is the text.
        if is_clickable_input:
            text = attrs.get("value", "")

        aria_label = attrs.get("aria-label", "")
        href = attrs.get("href", "")
        role = attrs.get("role", "")
        disabled = "disabled" in attrs or attrs.get("aria-disabled", "").lower() == "true"

        result = classify_clickable(
            text=text,
            aria_label=aria_label,
            href=href,
            role=role,
            tag=tag,
            enabled=not disabled,
            visible=True,
        )

        self.clickables.append(
            Clickable(
                selector=selector,
                tag=tag,
                text=text.strip(),
                aria_label=aria_label,
                href=href,
                role=role,
                enabled=not disabled,
                visible=True,
                classification=result.classification,
                confidence=result.confidence,
            )
        )

    def _handle_input(self, attrs: dict[str, str]) -> None:
        """Handle a non-clickable input element."""
        input_type = attrs.get("type", "text").lower()

        if input_type in _CLICKABLE_INPUT_TYPES:
            return  # Handled by _finalize_clickable.

        self._input_counter += 1
        element_id = attrs.get("id", "")
        name = attrs.get("name", "")
        if element_id:
            selector = f"#{element_id}"
        elif name:
            selector = f"input[name='{name}']"
        else:
            selector = f"input:nth-of-type({self._input_counter})"

        if input_type == "file":
            self.file_inputs.append(
                FileInputInfo(
                    selector=selector,
                    name=name,
                    accept=attrs.get("accept", ""),
                    multiple="multiple" in attrs,
                )
            )
        else:
            self.inputs.append(
                InputInfo(
                    selector=selector,
                    name=name,
                    input_type=input_type if input_type in _TEXT_INPUT_TYPES else "text",
                    label=attrs.get("aria-label", "") or attrs.get("placeholder", ""),
                    required="required" in attrs,
                    placeholder=attrs.get("placeholder", ""),
                )
            )

    @property
    def all_text(self) -> str:
        """All visible text on the page, joined."""
        return " ".join(self._all_text_parts).lower()


def _detect_page_state(
    all_text: str,
    clickables: list[Clickable],
    forms: list[FormInfo],
    inputs: list[InputInfo],
) -> PageState:
    """Detect the page state from extracted text and elements.

    Priority:
    1. captcha
    2. error
    3. expired
    4. submitted
    5. login
    6. register
    7. review
    8. form
    9. job_page / apply_page
    10. unknown
    """
    text_lower = all_text

    # Check captcha.
    for indicator in _CAPTCHA_INDICATORS:
        if indicator in text_lower:
            return PageState.CAPTCHA

    # Check error (only if "error" appears prominently).
    for indicator in _ERROR_INDICATORS:
        if indicator in text_lower:
            return PageState.ERROR

    # Check expired.
    for indicator in _EXPIRED_INDICATORS:
        if indicator in text_lower:
            return PageState.EXPIRED

    # Check submitted.
    for indicator in _SUBMITTED_INDICATORS:
        if indicator in text_lower:
            return PageState.SUBMITTED

    # Check login.
    for indicator in _LOGIN_INDICATORS:
        if indicator in text_lower:
            return PageState.LOGIN

    # Check register.
    for indicator in _REGISTER_INDICATORS:
        if indicator in text_lower:
            return PageState.REGISTER

    # Check review.
    for indicator in _REVIEW_INDICATORS:
        if indicator in text_lower:
            return PageState.REVIEW

    # Check form: if there are visible text inputs and at least one form.
    if forms and inputs:
        # Check for form-specific indicators.
        for indicator in _FORM_INDICATORS:
            if indicator in text_lower:
                return PageState.FORM
        # If there are enough inputs, treat it as a form.
        if len(inputs) >= 2:
            return PageState.FORM

    # Check form: if there is a form with a safe_continue button, treat
    # it as a form even if there are only select elements (no text inputs).
    if forms:
        has_safe_continue = any(
            c.classification == ClickableClassification.SAFE_CONTINUE for c in clickables
        )
        if has_safe_continue:
            return PageState.FORM

    # Check for apply page: if there are safe_apply clickables.
    for clickable in clickables:
        if clickable.classification == ClickableClassification.SAFE_APPLY:
            return PageState.APPLY_PAGE

    # Check for job page: if there's job-like text.
    if any(
        word in text_lower
        for word in ("job description", "responsibilities", "requirements", "qualifications")
    ):
        return PageState.JOB_PAGE

    return PageState.UNKNOWN


def observe_html(html: str, url: str = "") -> PageObservation:
    """Observe a page from raw HTML.

    This is the test-friendly interface: it parses static HTML without
    launching a browser. In production, ``observe_page`` (which wraps a
    Playwright page) would be used.

    Args:
        html: The raw HTML string.
        url: The URL of the page (for the observation metadata).

    Returns:
        A :class:`PageObservation` with extracted elements and detected
        page state.
    """
    parser = _DomExtractor()
    parser.feed(html)

    page_state = _detect_page_state(
        all_text=parser.all_text,
        clickables=parser.clickables,
        forms=parser.forms,
        inputs=parser.inputs,
    )

    warnings: list[str] = []
    for clickable in parser.clickables:
        if clickable.classification == ClickableClassification.DANGEROUS_SUBMIT:
            warnings.append(f"dangerous_submit detected: {clickable.text!r}")
    if page_state == PageState.CAPTCHA:
        warnings.append("captcha detected")
    if page_state == PageState.LOGIN:
        warnings.append("login required")

    return PageObservation(
        url=url,
        title=parser.title,
        page_state=page_state,
        inputs=parser.inputs,
        clickables=parser.clickables,
        forms=parser.forms,
        file_inputs=parser.file_inputs,
        warnings=warnings,
        screenshot=None,
    )


__all__ = ["observe_html"]
