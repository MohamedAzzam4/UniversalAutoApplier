"""Adapter interface for platform-specific application behavior.

Per ``ROADMAP.md`` WP 2.1, every adapter implements this interface. The
interface is intentionally minimal: ``can_handle`` routes a job to the
right adapter, and the four phase methods (``prepare``, ``navigate_to_form``,
``fill``, ``submit_or_pause``) return a structured :class:`AdapterResult`.

Adapters must:
- Return a structured :class:`AdapterResult` even when an exception happens.
- Never parse human-readable logs to determine success.
- Respect the review-before-submit safety rule: generic adapters must not
  submit without explicit approval.

The :class:`ApplicationAdapter` base class provides default implementations
that return ``AdapterResult.failed`` for every phase, so concrete adapters
only override the phases they actually support.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from universal_auto_applier.core.models import AdapterResult, ApplicationJob
from universal_auto_applier.core.statuses import Phase, Platform


class ApplicationAdapter(ABC):
    """Base class for all platform-specific adapters.

    Concrete adapters set ``platform`` to the :class:`Platform` they handle
    and override the phase methods they support. The default implementations
    return ``AdapterResult.failed`` so that calling an unsupported phase is
    safe and produces a structured result rather than a crash.

    ``is_trusted`` defaults to False. Only adapters that wrap a proven,
    trusted workflow (e.g. :class:`SiemensAdapter` wrapping the existing
    Siemens ``ApplyWorkflow``) should set ``is_trusted = True``. The
    pipeline orchestrator uses ``is_trusted`` to decide whether
    ``submit_or_pause`` may be called after the review gate approves.
    Untrusted adapters never submit, even when the review state is
    approved — their ``submit_or_pause`` always returns ``review_ready``.
    """

    platform: Platform = Platform.UNKNOWN
    is_trusted: bool = False

    @abstractmethod
    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if this adapter can handle ``job``.

        The registry calls this in registration order and selects the first
        adapter that returns True.
        """
        ...

    def prepare(self, job: ApplicationJob) -> AdapterResult:
        """Prepare to apply to ``job`` (e.g., validate documents, load profile).

        Default: not supported.
        """
        return AdapterResult.failed(
            phase=Phase.PREPARE,
            message=f"{self.platform} adapter does not implement prepare()",
            application_id=job.application_id,
            platform=self.platform,
        )

    def navigate_to_form(self, job: ApplicationJob) -> AdapterResult:
        """Navigate from the job URL to the application form.

        Default: not supported.
        """
        return AdapterResult.failed(
            phase=Phase.NAVIGATE,
            message=f"{self.platform} adapter does not implement navigate_to_form()",
            application_id=job.application_id,
            platform=self.platform,
        )

    def fill(self, job: ApplicationJob) -> AdapterResult:
        """Fill the application form.

        Default: not supported.
        """
        return AdapterResult.failed(
            phase=Phase.FILL,
            message=f"{self.platform} adapter does not implement fill()",
            application_id=job.application_id,
            platform=self.platform,
        )

    def submit_or_pause(self, job: ApplicationJob) -> AdapterResult:
        """Submit the application or pause for review.

        Default: not supported. Generic adapters must override this to
        always pause (review-before-submit) unless explicit approval is given.
        """
        return AdapterResult.failed(
            phase=Phase.SUBMIT,
            message=f"{self.platform} adapter does not implement submit_or_pause()",
            application_id=job.application_id,
            platform=self.platform,
        )


__all__ = ["ApplicationAdapter"]
