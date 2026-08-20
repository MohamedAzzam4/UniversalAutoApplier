"""Browser-side submit interlock for WQ-7 REAL_SITE_DRY_RUN mode.

This module installs a JavaScript interlock on every page BEFORE any site
scripts run. The interlock:

1. Prevents submit events from propagating (capture-phase listener).
2. Overrides ``HTMLFormElement.prototype.submit`` to record and block.
3. Overrides ``HTMLFormElement.prototype.requestSubmit`` to record and block.
4. Records every attempted submission with a global counter.

The interlock is installed via ``page.add_init_script()``, which runs
before any page JavaScript. This is the true lowest-layer defense —
even if a site has ``onchange="this.form.submit()"``, the override
prevents the submission.

The counters are readable via ``page.evaluate("__wq7_counters")``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

logger = logging.getLogger("universal_auto_applier.browser_interlock")

# JavaScript interlock script. Installed via page.add_init_script().
# Runs before ANY page JavaScript. Defines a global counter object and
# overrides form.submit/requestSubmit to record and block.
INTERLOCK_SCRIPT = """
(function() {
    // Global counter — readable via window.__wq7_counters
    window.__wq7_counters = {
        submit_events: 0,
        form_submit_calls: 0,
        request_submit_calls: 0,
        dispatch_submit_events: 0,
        blocked_submissions: 0,
        navigation_attempts: 0,
        authorized_submits: 0
    };
    window.__wq7_blocked_details = [];

    // WQ-8 one-shot authorized-submit allowance. While armed, the FIRST
    // submit signal is allowed through (consuming the allowance); every
    // later signal is blocked again. The coordinator arms it immediately
    // before its owner-authorized click and disarms it in a finally block,
    // so the allowance is never persistent.
    window.__wq8_submit_authorization = {armed: false, token: null};
    function consumeAuthorizedSubmit(present) {
        // present(token) records which layer consumed the allowance.
        var auth = window.__wq8_submit_authorization;
        if (auth && auth.armed) {
            auth.armed = false;
            if (present && auth.token) { present(auth.token); }
            auth.token = null;
            window.__wq7_counters.authorized_submits++;
            return true;
        }
        return false;
    }
    function grantAuthorizedSubmit(token) {
        window.__wq8_submit_authorization.armed = true;
        window.__wq8_submit_authorization.token = token;
        return true;
    }
    function clearAuthorizedSubmit() {
        window.__wq8_submit_authorization.armed = false;
        window.__wq8_submit_authorization.token = null;
        return true;
    }
    // Exposed for the coordinator (arm/disarm are one-time evaluate calls).
    window.__wq8_arm_submit_authorization = grantAuthorizedSubmit;
    window.__wq8_clear_submit_authorization = clearAuthorizedSubmit;

    // 1. Capture-phase submit event listener — blocks ALL submit events.
    document.addEventListener('submit', function(e) {
        if (consumeAuthorizedSubmit()) {
            // Exactly one owner-authorized submit passes through.
            return;
        }
        window.__wq7_counters.submit_events++;
        window.__wq7_counters.blocked_submissions++;
        window.__wq7_blocked_details.push({
            type: 'submit_event',
            target: e.target ? (e.target.id || e.target.name || e.target.tagName) : 'unknown',
            timestamp: Date.now()
        });
        e.preventDefault();
        e.stopPropagation();
        return false;
    }, true); // true = capture phase (runs before site handlers)

    // 2. Override HTMLFormElement.prototype.submit
    var origSubmit = HTMLFormElement.prototype.submit;
    var origRequestSubmit = HTMLFormElement.prototype.requestSubmit;
    HTMLFormElement.prototype.submit = function() {
        if (consumeAuthorizedSubmit()) {
            return origSubmit.apply(this, arguments);
        }
        window.__wq7_counters.form_submit_calls++;
        window.__wq7_counters.blocked_submissions++;
        window.__wq7_blocked_details.push({
            type: 'form_submit',
            target: this.id || this.name || this.action || 'unknown',
            timestamp: Date.now()
        });
        // Do NOT call origSubmit — block completely.
        return undefined;
    };

    // 2b. Use a MutationObserver to catch forms added dynamically and
    // freeze their submit property. Also freeze existing forms.
    function freezeFormSubmit(form) {
        try {
            // Freeze form.submit
            Object.defineProperty(form, 'submit', {
                value: function() {
                    if (consumeAuthorizedSubmit()) {
                        return origSubmit.apply(form, arguments);
                    }
                    window.__wq7_counters.form_submit_calls++;
                    window.__wq7_counters.blocked_submissions++;
                    window.__wq7_blocked_details.push({
                        type: 'form_submit',
                        target: form.id || form.name || form.action || 'unknown',
                        timestamp: Date.now()
                    });
                    return undefined;
                },
                writable: false,
                configurable: false
            });
            // Freeze form.requestSubmit if it exists
            if (typeof form.requestSubmit === 'function') {
                Object.defineProperty(form, 'requestSubmit', {
                    value: function() {
                        if (consumeAuthorizedSubmit()) {
                            return origRequestSubmit.apply(form, arguments);
                        }
                        window.__wq7_counters.request_submit_calls++;
                        window.__wq7_counters.blocked_submissions++;
                        window.__wq7_blocked_details.push({
                            type: 'request_submit',
                            target: form.id || form.name || form.action || 'unknown',
                            timestamp: Date.now()
                        });
                        return undefined;
                    },
                    writable: false,
                    configurable: false
                });
            }
            // Remove inline onsubmit handler
            form.removeAttribute('onsubmit');
            form.onsubmit = null;
        } catch(e) { /* already frozen or not a form */ }
    }

    // Freeze all existing forms
    function freezeAllForms() {
        document.querySelectorAll('form').forEach(freezeFormSubmit);
    }

    // Run immediately (might be before DOM is ready)
    freezeAllForms();

    // Also run on DOMContentLoaded (after DOM is parsed, before site scripts
    // that might try to override form.submit)
    document.addEventListener('DOMContentLoaded', freezeAllForms, true);

    // Watch for dynamically added forms
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            m.addedNodes.forEach(function(node) {
                if (node.tagName === 'FORM') freezeFormSubmit(node);
                if (node.querySelectorAll) {
                    node.querySelectorAll('form').forEach(freezeFormSubmit);
                }
            });
        });
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});

    // 3. Override HTMLFormElement.prototype.requestSubmit
    if (HTMLFormElement.prototype.requestSubmit) {
        HTMLFormElement.prototype.requestSubmit = function() {
            if (consumeAuthorizedSubmit()) {
                return origRequestSubmit.apply(this, arguments);
            }
            window.__wq7_counters.request_submit_calls++;
            window.__wq7_counters.blocked_submissions++;
            window.__wq7_blocked_details.push({
                type: 'request_submit',
                target: this.id || this.name || this.action || 'unknown',
                timestamp: Date.now()
            });
            // Do NOT call origRequestSubmit — block completely.
            return undefined;
    };
    }

    // 4. Intercept dispatchEvent for SubmitEvent.
    // Note: we use a simple assignment here (not defineProperty) because
    // EventTarget.prototype.dispatchEvent may already be non-configurable
    // in some browsers. The capture-phase submit listener (#1) is the
    // authoritative block — this dispatchEvent override is defense-in-depth.
    var origDispatchEvent = EventTarget.prototype.dispatchEvent;
    EventTarget.prototype.dispatchEvent = function(event) {
        if (event && event.type === 'submit') {
            if (consumeAuthorizedSubmit()) {
                return origDispatchEvent.call(this, event);
            }
            window.__wq7_counters.dispatch_submit_events++;
            window.__wq7_counters.blocked_submissions++;
            window.__wq7_blocked_details.push({
                type: 'dispatch_submit_event',
                target: this.id || this.name || this.tagName || 'unknown',
                timestamp: Date.now()
            });
            return false; // Block
        }
        return origDispatchEvent.call(this, event);
    };

    // 5. Intercept beforeunload to detect navigation caused by submission
    window.addEventListener('beforeunload', function(e) {
        window.__wq7_counters.navigation_attempts++;
    });

    // 6. Mark the interlock as installed
    window.__wq7_interlock_installed = true;
})();
"""


def install_interlock(page: BrowserContext | Page) -> None:
    """Install the submit interlock on a page before any site scripts run.

    This must be called via ``page.add_init_script()`` BEFORE navigating
    to any page. The interlock persists across all navigations within the
    same browser context.

    Args:
        page: A Playwright Page or BrowserContext. If a Page is passed,
            the script is added to that page. If a BrowserContext is
            passed, the script is added to all pages in the context.
    """
    try:
        # BrowserContext.add_init_script is preferred — it applies to
        # all pages in the context, including new tabs/popups.
        if hasattr(page, "add_init_script"):
            page.add_init_script(INTERLOCK_SCRIPT)
            logger.info("[wq7-interlock] Submit interlock installed")
        else:
            logger.warning("[wq7-interlock] Object does not support add_init_script")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[wq7-interlock] Failed to install interlock: %s", exc)


def read_counters(page: Page) -> dict[str, int]:
    """Read the interlock counters from a page.

    Returns a dict with:
    - submit_events: number of submit events intercepted
    - form_submit_calls: number of form.submit() calls blocked
    - request_submit_calls: number of requestSubmit() calls blocked
    - dispatch_submit_events: number of dispatched submit events blocked
    - blocked_submissions: total blocked submission attempts
    - navigation_attempts: number of beforeunload events
    - authorized_submits: number of one-shot owner-authorized passes
    """
    try:
        result = page.evaluate(  # type: ignore[attr-defined]
            "() => window.__wq7_counters || {submit_events: 0, form_submit_calls: 0, "
            "request_submit_calls: 0, dispatch_submit_events: 0, blocked_submissions: 0, "
            "navigation_attempts: 0, authorized_submits: 0}"
        )
        return result
    except Exception:  # noqa: BLE001
        return {
            "submit_events": 0,
            "form_submit_calls": 0,
            "request_submit_calls": 0,
            "dispatch_submit_events": 0,
            "blocked_submissions": 0,
            "navigation_attempts": 0,
            "authorized_submits": 0,
        }


def arm_authorized_submit(page: Page, token: str) -> bool:
    """Arm the one-shot authorized-submit allowance for exactly one signal.

    WQ-8 only: the coordinator arms this immediately before its
    owner-authorized submit click and disarms it in a ``finally``. While
    armed, the FIRST submit signal (submit event, ``form.submit()``,
    ``requestSubmit()``, or dispatched submit event) passes through; every
    later signal is blocked again. Returns True on success.
    """
    try:
        page.evaluate(  # type: ignore[attr-defined]
            "() => !!window.__wq8_arm_submit_authorization(" + json.dumps(token) + ")"
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[wq7-interlock] Failed to arm authorized submit allowance")
        return False


def disarm_authorized_submit(page: Page) -> bool:
    """Clear the one-shot authorized-submit allowance.

    Called in a ``finally`` so the allowance can never leak past the
    coordinator's click. Returns True on success.
    """
    try:
        page.evaluate(  # type: ignore[attr-defined]
            "() => !!window.__wq8_clear_submit_authorization()"
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("[wq7-interlock] Failed to disarm authorized submit allowance")
        return False


def read_blocked_details(page: Page) -> list[dict[str, Any]]:
    """Read the detailed blocked submission records from a page."""
    try:
        result = page.evaluate("() => window.__wq7_blocked_details || []")  # type: ignore[attr-defined]
        return result
    except Exception:  # noqa: BLE001
        return []


def is_interlock_installed(page: Page) -> bool:
    """Check whether the interlock is installed on a page."""
    try:
        # Check via the counters object — if it exists, the interlock ran.
        result = page.evaluate("() => typeof window.__wq7_counters !== 'undefined'")
        return bool(result)
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "INTERLOCK_SCRIPT",
    "arm_authorized_submit",
    "disarm_authorized_submit",
    "install_interlock",
    "read_counters",
    "read_blocked_details",
    "is_interlock_installed",
]
