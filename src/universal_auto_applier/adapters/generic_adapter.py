"""Generic fallback adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` WP 2.2 and Phase 7 ("Generic fallback improvements"),
the generic adapter is the fallback when no known adapter matches. It
is **untrusted**: it never submits, and always pauses at
``review_before_submit``.

Phase 7 improvements (over the Phase 2 stub):
- ``can_handle`` still always returns True (it is the fallback).
- ``prepare``, ``navigate_to_form``, ``fill``, and ``submit_or_pause``
  now have real implementations that use the shared Phase 3/4/5
  infrastructure (:func:`observe_html`, :func:`safe_explore`,
  :func:`extract_form_fields`, :func:`fill_form`).
- ``submit_or_pause`` ALWAYS returns ``review_ready`` and creates a
  ``review_before_submit`` intervention note. It never clicks a submit
  button.
- On layout change / missing selector / unknown page state, the adapter
  fails safely with a structured :class:`AdapterResult`.

The generic adapter is the safety net for any ATS that does not have a
dedicated adapter. Because it is untrusted, it cannot submit even if
the user explicitly approves a review state — only the orchestrator's
review gate (which checks ``is_trusted``) can authorize submission, and
the generic adapter's ``is_trusted`` is False.

Safety (per ADR-001 D5):
- Generic adapter never auto-submits.
- Review-before-submit is the default and cannot be bypassed for generic.
- Every step records a structured ``AdapterResult`` and evidence.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class GenericAdapter(_UntrustedATSAdapter):
    """Fallback adapter for unknown platforms.

    The generic adapter can handle any job — it is the fallback when no
    other adapter's ``can_handle`` returns True. It uses the shared
    Phase 3/4/5 infrastructure for navigation, form filling, and
    interventions, and never submits.

    Safety (per ADR-001 D5):
    - ``is_trusted`` is False.
    - ``submit_or_pause`` always returns ``review_ready``.
    - Review-before-submit is the default and cannot be bypassed.
    """

    platform = Platform.GENERIC

    def can_handle(self, job: ApplicationJob) -> bool:
        """The generic adapter can handle any job — it is the fallback."""
        return True


__all__ = ["GenericAdapter"]
