"""Generic fallback adapter.

Per ``ROADMAP.md`` WP 2.2, the generic adapter is the fallback when no
known adapter matches. It is intentionally a placeholder in Phase 2 — the
actual generic navigation and form filling logic lands in Phase 3+.

The generic adapter:
- Claims any job that no other adapter claims (``can_handle`` always True).
- Returns ``AdapterResult.unsupported`` for all phases, because generic
  navigation/form filling is not implemented yet.
- Will never auto-submit (review-before-submit is the default).
"""

from __future__ import annotations

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.core.models import AdapterResult, ApplicationJob
from universal_auto_applier.core.statuses import AdapterResultStatus, Phase, Platform


class GenericAdapter(ApplicationAdapter):
    """Fallback adapter for unknown platforms.

    Phase 3+ will add real navigation and form filling. For now, all phases
    return ``unsupported`` so callers get a structured result instead of a
    crash.
    """

    platform = Platform.GENERIC

    def can_handle(self, job: ApplicationJob) -> bool:
        """The generic adapter can handle any job — it is the fallback."""
        return True

    def prepare(self, job: ApplicationJob) -> AdapterResult:
        return AdapterResult(
            status=AdapterResultStatus.UNSUPPORTED,
            phase=Phase.PREPARE,
            message="Generic adapter does not implement prepare() yet (Phase 3+)",
            application_id=job.application_id,
            platform=self.platform,
        )

    def navigate_to_form(self, job: ApplicationJob) -> AdapterResult:
        return AdapterResult(
            status=AdapterResultStatus.UNSUPPORTED,
            phase=Phase.NAVIGATE,
            message="Generic adapter does not implement navigate_to_form() yet (Phase 3+)",
            application_id=job.application_id,
            platform=self.platform,
        )

    def fill(self, job: ApplicationJob) -> AdapterResult:
        return AdapterResult(
            status=AdapterResultStatus.UNSUPPORTED,
            phase=Phase.FILL,
            message="Generic adapter does not implement fill() yet (Phase 4+)",
            application_id=job.application_id,
            platform=self.platform,
        )

    def submit_or_pause(self, job: ApplicationJob) -> AdapterResult:
        return AdapterResult(
            status=AdapterResultStatus.UNSUPPORTED,
            phase=Phase.SUBMIT,
            message="Generic adapter does not implement submit_or_pause() yet (Phase 5+)",
            application_id=job.application_id,
            platform=self.platform,
        )


__all__ = ["GenericAdapter"]
