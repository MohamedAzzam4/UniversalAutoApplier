"""Default-deny HTTP mutation guard for browser preparation runs.

The guard is installed on the browser context before UAA creates a page. It
allows ordinary read navigation (GET/HEAD/OPTIONS) and aborts every other HTTP
method. Evidence deliberately omits paths, query strings, headers, and bodies.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, CDPSession, Page, Route

from universal_auto_applier.browser.live_models import BlockedHttpRequest

logger = logging.getLogger("universal_auto_applier.browser.request_interlock")

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MAX_BLOCKED_EVIDENCE = 200

_SERVICE_WORKER_GUARD_SCRIPT = """
(() => {
  const serviceWorker = navigator.serviceWorker;
  if (!serviceWorker) {
    window.__uaa_service_worker_guard = 'unavailable';
    return;
  }
  const denyRegistration = () => Promise.reject(
    new DOMException('Service worker registration is disabled during preparation.', 'SecurityError')
  );
  try {
    const prototype = Object.getPrototypeOf(serviceWorker);
    Object.defineProperty(prototype, 'register', {
      value: denyRegistration, configurable: false, writable: false
    });
    Object.defineProperty(serviceWorker, 'register', {
      value: denyRegistration, configurable: false, writable: false
    });
    window.__uaa_service_worker_guard = 'installed';
  } catch (_) {
    window.__uaa_service_worker_guard = 'failed';
  }
})();
"""


class RequestInterlockSetupError(RuntimeError):
    """Raised when request protection cannot be established before navigation."""


class PreparationRequestInterlock:
    """Protect one preparation browser context and retain sanitized evidence."""

    def __init__(self, context: BrowserContext, *, application_id: str) -> None:
        self._context = context
        self._application_id = application_id[:12]
        self.blocked_requests: list[BlockedHttpRequest] = []
        self.blocked_request_count = 0
        self.installed = False
        self.setup_error: str | None = None
        self._service_worker_seen = False
        self._attached_page_ids: set[int] = set()
        self._cdp_sessions: list[CDPSession] = []

    def install(self) -> None:
        """Install service-worker and network guards before any page is made."""
        try:
            running_workers = self._context.service_workers
            existing_pages = self._context.pages
        except Exception as exc:  # noqa: BLE001
            raise RequestInterlockSetupError(
                "cannot verify page and service-worker state for preparation context"
            ) from exc
        if running_workers:
            raise RequestInterlockSetupError(
                "preparation refused because the browser context has active service workers"
            )
        if existing_pages:
            raise RequestInterlockSetupError(
                "preparation refused because the browser context already has pages"
            )

        try:
            # Block newly registered workers even when run_in_context receives
            # a caller-owned context that was not created by LiveBrowserRunner.
            self._context.add_init_script(_SERVICE_WORKER_GUARD_SCRIPT)
            self._context.on("page", self._on_page)
            self._context.on("serviceworker", self._on_service_worker)
            # Context routing applies to every page and popup in this context.
            # Install it before UAA creates its first page or navigates.
            self._context.route("**/*", self._handle_route)
        except Exception as exc:  # noqa: BLE001
            raise RequestInterlockSetupError(
                "failed to install preparation network interlock before navigation"
            ) from exc
        self.installed = True

    def attach_page(self, page: Page) -> None:
        """Disable service-worker interception for a page before it navigates."""
        page_id = id(page)
        if page_id in self._attached_page_ids:
            return
        try:
            session = self._context.new_cdp_session(page)
            cast(Any, session).send("Network.setBypassServiceWorker", {"bypass": True})
        except Exception as exc:  # noqa: BLE001
            self.setup_error = "page_service_worker_bypass_failed"
            raise RequestInterlockSetupError(
                "cannot verify service-worker bypass for preparation page"
            ) from exc
        self._cdp_sessions.append(session)
        self._attached_page_ids.add(page_id)

    def verify_page(self, page: Page) -> None:
        """Fail closed if a page or service-worker guard was not established."""
        if not self.installed:
            raise RequestInterlockSetupError("preparation network interlock is not installed")
        if id(page) not in self._attached_page_ids:
            raise RequestInterlockSetupError("preparation page service-worker bypass is not armed")
        try:
            if self._context.service_workers:
                self.setup_error = "active_service_worker_detected"
        except Exception as exc:  # noqa: BLE001
            self.setup_error = "service_worker_state_unavailable"
            raise RequestInterlockSetupError(
                "cannot verify service-worker state during preparation"
            ) from exc
        if self.setup_error is not None:
            raise RequestInterlockSetupError(
                "preparation stopped because a browser safety guard failed"
            )

    def verify_service_worker_registration_guard(self, page: Page) -> None:
        """Verify the pre-navigation init script ran and denied worker registration."""
        try:
            result = page.evaluate("window.__uaa_service_worker_guard")
        except Exception as exc:  # noqa: BLE001
            self.setup_error = "service_worker_guard_script_not_observable"
            raise RequestInterlockSetupError(
                "cannot verify service-worker registration guard before navigation"
            ) from exc
        if result not in {"installed", "unavailable"}:
            self.setup_error = "service_worker_registration_guard_missing"
            raise RequestInterlockSetupError(
                "service-worker registration guard did not run before target navigation"
            )

    def _on_page(self, page: Page) -> None:
        """Arm bypass on popups; failures make every later request fail closed."""
        try:
            self.attach_page(page)
        except RequestInterlockSetupError:
            # The route callback checks setup_error and aborts all requests.
            return

    def _on_service_worker(self, _worker: object) -> None:
        """Treat a newly appearing service worker as an unverified context."""
        self._service_worker_seen = True
        self.setup_error = "unexpected_service_worker"

    def _handle_route(self, route: Route) -> None:
        """Continue read-only requests; abort every mutating/unknown request."""
        try:
            request = route.request
            method = request.method.upper()
            resource_type = request.resource_type or "unknown"
            origin = _safe_origin(request.url)
        except Exception:  # noqa: BLE001
            self._record_block(
                method="UNKNOWN",
                resource_type="unknown",
                destination_origin="unknown-origin",
                reason="request_inspection_failed",
            )
            self._abort_route(route)
            return

        if self.setup_error is not None or self._service_worker_seen:
            self._record_block(
                method=method,
                resource_type=resource_type,
                destination_origin=origin,
                reason="safety_guard_unverified",
            )
            self._abort_route(route)
            return

        if method not in _READ_ONLY_METHODS:
            self._record_block(
                method=method,
                resource_type=resource_type,
                destination_origin=origin,
                reason="mutating_method",
            )
            self._abort_route(route)
            return

        try:
            route.continue_()
        except Exception:  # noqa: BLE001
            # Continuation failure leaves delivery uncertain even for a GET:
            # endpoints can have side effects. Mark the outcome unknown before
            # trying to abort so later readiness checks cannot pass.
            self.setup_error = "request_outcome_unknown"
            self._record_block(
                method=method,
                resource_type=resource_type,
                destination_origin=origin,
                reason="safe_request_continuation_failed",
            )
            self._abort_route(route)

    def _abort_route(self, route: Route) -> None:
        """Abort a denied request and fail the run if route handling is uncertain."""
        try:
            route.abort(error_code="blockedbyclient")
        except Exception:  # noqa: BLE001
            # An abort failure means a request that policy intended to block
            # may have reached the remote server. Preserve that uncertainty
            # over the initiating route error so reports require reconciliation.
            self.setup_error = "request_abort_failed"

    def _record_block(
        self,
        *,
        method: str,
        resource_type: str,
        destination_origin: str,
        reason: str,
    ) -> None:
        self.blocked_request_count += 1
        if len(self.blocked_requests) < _MAX_BLOCKED_EVIDENCE:
            self.blocked_requests.append(
                BlockedHttpRequest(
                    method=method[:16],
                    resource_type=resource_type[:32],
                    destination_origin=destination_origin,
                    reason=reason,
                )
            )
        logger.warning(
            "[%s] preparation http_request_interlock blocked method=%s resource_type=%s origin=%s reason=%s",
            self._application_id,
            method[:16],
            resource_type[:32],
            destination_origin,
            reason,
        )


def _safe_origin(url: str) -> str:
    """Return scheme/host/port only; never persist a path, query, or fragment."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return "unknown-origin"
        host = parts.hostname.lower()
        port = parts.port
        if port is not None:
            host = f"{host}:{port}"
        return f"{parts.scheme.lower()}://{host}"
    except (TypeError, ValueError):
        return "unknown-origin"


__all__ = ["PreparationRequestInterlock", "RequestInterlockSetupError"]
