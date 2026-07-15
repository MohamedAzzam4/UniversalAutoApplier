"""Live DOM analysis and safe apply-path navigation for Playwright pages."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import (
    BrowserContext,
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

from universal_auto_applier.core.statuses import ClickableClassification
from universal_auto_applier.navigator.clickable_classifier import classify_clickable

logger = logging.getLogger("universal_auto_applier.navigator.apply_path_finder")

_CLICKABLE_SELECTOR = "button, a[href], input[type='button'], input[type='submit'], [role='button']"
_CONTROL_SELECTOR = (
    "input:not([type='hidden']):not([type='button']):not([type='submit'])"
    ":not([type='reset']):not([type='image']), textarea, select"
)
_APPLICATION_FIELD_TERMS = (
    "first name",
    "firstname",
    "last name",
    "lastname",
    "email",
    "phone",
    "resume",
    "cv",
    "cover letter",
    "vorname",
    "nachname",
    "telefon",
    "lebenslauf",
)
_CAPTCHA_TERMS = (
    "captcha",
    "verify you are human",
    "are you human",
    "human verification",
)
_SECURITY_TERMS = (
    "access denied",
    "checking your browser",
    "unusual activity",
    "automated requests",
    "temporarily blocked",
    "security verification",
)
_PAYMENT_TERMS = (
    "payment required",
    "credit card",
    "card number",
    "billing information",
)
_SUBMITTED_TERMS = (
    "application submitted",
    "successfully submitted",
    "thank you for applying",
    "application received",
    "bewerbung eingegangen",
)
_EXPIRED_TERMS = (
    "job expired",
    "position no longer available",
    "job is no longer available",
    "stelle nicht mehr verfugbar",
)


@dataclass
class LiveClickable:
    """A visible, enabled Playwright locator with a safety classification."""

    locator: Locator
    text: str
    aria_label: str
    selector_hint: str
    frame_url: str
    href: str
    target: str
    classification: ClickableClassification
    confidence: float


@dataclass
class LivePageAnalysis:
    """Current browser state derived from rendered DOM data."""

    url: str
    title: str
    clickables: list[LiveClickable] = field(default_factory=list[LiveClickable])
    is_application_form: bool = False
    has_dangerous_submit: bool = False
    blocker: str | None = None
    submitted: bool = False
    expired: bool = False
    visible_control_count: int = 0
    file_input_count: int = 0


def _safe_inner_text(locator: Locator, *, timeout: float = 1_000) -> str:
    try:
        return locator.inner_text(timeout=timeout).strip()
    except Exception:
        return ""


def _safe_attribute(locator: Locator, name: str) -> str:
    try:
        return locator.get_attribute(name, timeout=1_000) or ""
    except Exception:
        return ""


def _selector_hint(locator: Locator, index: int) -> str:
    element_id = _safe_attribute(locator, "id")
    if element_id:
        return f"[id={element_id!r}]"
    name = _safe_attribute(locator, "name")
    if name:
        return f"[name={name!r}]"
    test_id = _safe_attribute(locator, "data-testid") or _safe_attribute(locator, "data-test")
    if test_id:
        return f"[data-test={test_id!r}]"
    role = _safe_attribute(locator, "role")
    return f"{role or 'clickable'}[{index}]"


def _collect_clickables(frame: Frame) -> list[LiveClickable]:
    found: list[LiveClickable] = []
    locators = frame.locator(_CLICKABLE_SELECTOR)
    try:
        count = min(locators.count(), 200)
    except Exception:
        return found

    for index in range(count):
        locator = locators.nth(index)
        try:
            if not locator.is_visible() or not locator.is_enabled():
                continue
        except Exception:
            continue

        text = _safe_inner_text(locator) or _safe_attribute(locator, "value")
        aria_label = _safe_attribute(locator, "aria-label") or _safe_attribute(locator, "title")
        result = classify_clickable(
            text=text,
            aria_label=aria_label,
            href=_safe_attribute(locator, "href"),
            role=_safe_attribute(locator, "role"),
            tag=_safe_attribute(locator, "tagName"),
            enabled=True,
            visible=True,
        )
        found.append(
            LiveClickable(
                locator=locator,
                text=text[:200],
                aria_label=aria_label[:200],
                selector_hint=_selector_hint(locator, index),
                frame_url=frame.url,
                href=_safe_attribute(locator, "href"),
                target=_safe_attribute(locator, "target"),
                classification=result.classification,
                confidence=result.confidence,
            )
        )
    return found


def _frame_text(frame: Frame) -> str:
    try:
        return frame.locator("body").inner_text(timeout=2_000).lower()
    except Exception:
        return ""


def _application_control_counts(frame: Frame) -> tuple[int, int, int]:
    """Return visible controls, file inputs, and application-field signals."""
    controls = frame.locator(_CONTROL_SELECTOR)
    try:
        count = min(controls.count(), 250)
    except Exception:
        return 0, 0, 0

    visible = 0
    files = 0
    signals = 0
    for index in range(count):
        locator = controls.nth(index)
        input_type = _safe_attribute(locator, "type").lower()
        is_file = input_type == "file"
        try:
            is_visible = locator.is_visible()
        except Exception:
            is_visible = False
        if not is_visible and not is_file:
            continue
        visible += 1
        if is_file:
            files += 1

        descriptor = " ".join(
            (
                _safe_attribute(locator, "name"),
                _safe_attribute(locator, "id"),
                _safe_attribute(locator, "placeholder"),
                _safe_attribute(locator, "aria-label"),
                _safe_attribute(locator, "autocomplete"),
                input_type,
            )
        ).lower()
        if any(term in descriptor for term in _APPLICATION_FIELD_TERMS):
            signals += 1
    return visible, files, signals


def _has_visible(frame: Frame, selector: str) -> bool:
    locators = frame.locator(selector)
    try:
        count = min(locators.count(), 20)
    except Exception:
        return False
    for index in range(count):
        try:
            if locators.nth(index).is_visible():
                return True
        except Exception:
            continue
    return False


def _detect_blocker(page: Page, text: str) -> str | None:
    frames = page.frames
    if any(term in text for term in _CAPTCHA_TERMS) or any(
        _has_visible(
            frame,
            "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
            ".g-recaptcha, .h-captcha, [data-sitekey]",
        )
        for frame in frames
    ):
        return "captcha_detected"

    if any(term in text for term in _SECURITY_TERMS):
        return "security_wall"

    if any(term in text for term in _PAYMENT_TERMS) or any(
        _has_visible(frame, "input[autocomplete^='cc-'], input[name*='card' i]") for frame in frames
    ):
        return "payment_required"

    password_visible = any(_has_visible(frame, "input[type='password']") for frame in frames)
    login_dialog_visible = False
    for frame in frames:
        dialogs = frame.locator("[role='dialog'], dialog")
        try:
            dialog_count = min(dialogs.count(), 10)
        except Exception:
            continue
        for index in range(dialog_count):
            dialog = dialogs.nth(index)
            try:
                if not dialog.is_visible():
                    continue
                dialog_text = dialog.inner_text(timeout=1_000).lower()
            except Exception:
                continue
            if any(term in dialog_text for term in ("sign in", "log in", "login")):
                login_dialog_visible = True
                break
        if login_dialog_visible:
            break
    login_path = any(part in page.url.lower() for part in ("/login", "/signin", "/sign-in"))
    login_heading = any(
        term in text for term in ("sign in to continue", "log in to continue", "login required")
    )
    if password_visible or login_dialog_visible or (login_path and login_heading):
        return "login_required"
    return None


def analyze_page(page: Page) -> LivePageAnalysis:
    """Inspect the rendered page and classify its current application state."""
    all_text_parts: list[str] = []
    clickables: list[LiveClickable] = []
    visible_controls = 0
    file_inputs = 0
    application_signals = 0
    visible_forms = 0

    for frame in page.frames:
        all_text_parts.append(_frame_text(frame))
        clickables.extend(_collect_clickables(frame))
        controls, files, signals = _application_control_counts(frame)
        visible_controls += controls
        file_inputs += files
        application_signals += signals
        if _has_visible(frame, "form"):
            visible_forms += 1

    all_text = " ".join(all_text_parts)
    has_submit = any(
        item.classification == ClickableClassification.DANGEROUS_SUBMIT for item in clickables
    )
    has_continue = any(
        item.classification == ClickableClassification.SAFE_CONTINUE for item in clickables
    )
    application_url_signal = any(
        term in frame.url.lower()
        for frame in page.frames
        for term in ("/apply", "/application", "/applications/")
    )
    is_form = (
        file_inputs > 0
        or (visible_controls >= 2 and application_signals >= 1)
        or (
            visible_forms > 0
            and visible_controls >= 1
            and application_signals >= 1
            and (has_continue or has_submit)
        )
        or (visible_forms > 0 and visible_controls >= 1 and application_url_signal)
    )

    return LivePageAnalysis(
        url=page.url,
        title=page.title(),
        clickables=clickables,
        is_application_form=is_form,
        has_dangerous_submit=has_submit,
        blocker=_detect_blocker(page, all_text),
        submitted=any(term in all_text for term in _SUBMITTED_TERMS),
        expired=any(term in all_text for term in _EXPIRED_TERMS),
        visible_control_count=visible_controls,
        file_input_count=file_inputs,
    )


def choose_safe_action(
    analysis: LivePageAnalysis,
    *,
    allow_apply: bool,
    allow_continue: bool,
) -> LiveClickable | None:
    """Select the highest-priority allowed action; never select submit or unknown."""
    priorities: dict[ClickableClassification, int] = {}
    if allow_apply:
        priorities[ClickableClassification.SAFE_APPLY] = 0
    if allow_continue:
        priorities[ClickableClassification.SAFE_CONTINUE] = 1

    candidates = [item for item in analysis.clickables if item.classification in priorities]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (priorities[item.classification], -item.confidence))
    return candidates[0]


def choose_safe_classification(
    classifications: list[ClickableClassification],
    *,
    allow_apply: bool,
    allow_continue: bool,
) -> ClickableClassification | None:
    """Pure navigation-policy helper used by unit tests."""
    priorities: list[ClickableClassification] = []
    if allow_apply:
        priorities.append(ClickableClassification.SAFE_APPLY)
    if allow_continue:
        priorities.append(ClickableClassification.SAFE_CONTINUE)
    return next((item for item in priorities if item in classifications), None)


def click_action(
    context: BrowserContext,
    current_page: Page,
    action: LiveClickable,
    *,
    timeout_ms: int,
) -> Page:
    """Click one approved action and return the active page after navigation."""
    before_pages = list(context.pages)
    active_page: Page | None = None
    if action.target.lower() == "_blank":
        try:
            with context.expect_page(timeout=timeout_ms) as page_info:
                action.locator.click(timeout=timeout_ms)
            active_page = page_info.value
        except PlaywrightTimeoutError:
            # Some tracked ATS links suppress/delay the popup. Following the
            # element's own HTTP(S) href is deterministic and equivalent to
            # the approved click; arbitrary page URLs are never invented.
            resolved_href = urljoin(current_page.url, action.href)
            href_parts = urlsplit(resolved_href)
            if href_parts.scheme not in {"http", "https"} or not href_parts.hostname:
                raise
            current_page.goto(
                resolved_href,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            active_page = current_page
    else:
        action.locator.click(timeout=timeout_ms)

    try:
        current_page.wait_for_timeout(500)
    except Exception:
        pass

    if active_page is None:
        new_pages = [page for page in context.pages if page not in before_pages]
        if new_pages:
            active_page = new_pages[-1]
        elif current_page.is_closed():
            if not context.pages:
                raise PlaywrightTimeoutError("click closed the page without opening a replacement")
            active_page = context.pages[-1]
        else:
            active_page = current_page

    try:
        active_page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        logger.info("page did not reach domcontentloaded before timeout: %s", active_page.url)
    try:
        active_page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
    except PlaywrightTimeoutError:
        # Many ATS pages keep analytics/network connections open indefinitely.
        pass
    return active_page


__all__ = [
    "LiveClickable",
    "LivePageAnalysis",
    "analyze_page",
    "choose_safe_action",
    "choose_safe_classification",
    "click_action",
]
