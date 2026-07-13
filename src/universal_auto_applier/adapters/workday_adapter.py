"""Workday adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` Phase 7, this adapter handles Workday-hosted
application pages. It is **untrusted**: it never submits, and always
pauses at ``review_before_submit``.

Supported URL patterns (from ``DATA_CONTRACTS.md``):

    myworkdayjobs.com
    *.myworkdayjobs.com

Navigation behavior:
- Workday job pages typically have an "Apply" button that navigates to
  a multi-step application flow. The adapter uses the shared
  :func:`observe_html` and :func:`safe_explore` infrastructure to find
  the apply button and reach the form.

Login behavior:
- Workday applications often **do** require login. If a login page is
  detected (URL contains ``/login`` or page text contains "Sign In"),
  the adapter stops and creates a ``login_required`` intervention.
- The adapter never submits credentials and never bypasses login.

Form handling strategy:
- Uses the shared :func:`extract_form_fields` and :func:`fill_form`.
- Workday forms are typically multi-step. The fixture-based tests are
  single-page only; multi-step navigation is a known limitation.
- File inputs are mapped to ``job.cv_pdf`` and ``job.cover_letter_pdf``.

Submit safety:
- ``submit_or_pause`` ALWAYS returns ``review_ready``. The adapter
  never clicks the "Submit" button.

Failure behavior:
- If the page layout changes (e.g. Workday updates their DOM), the
  adapter fails safely with a structured ``AdapterResult``.

Test fixtures:
- ``tests/fixtures/platforms/workday_login.html`` (login page, already exists)
- ``tests/fixtures/platforms/workday_job.html`` (job page)
- ``tests/fixtures/platforms/workday_apply.html`` (apply form)
- ``tests/fixtures/platforms/workday_review.html`` (review page)
- ``tests/fixtures/platforms/workday_changed_layout.html`` (changed layout)

Known limitations:
- Does not handle Workday's multi-step application flow (Next/Continue
  buttons across multiple pages). Fixture tests are single-page.
- Does not handle Workday's SSO / SAML login redirects.
- Does not bypass login or captcha. Both stop the adapter.
- Does not store or submit credentials.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class WorkdayAdapter(_UntrustedATSAdapter):
    """Untrusted adapter for Workday-hosted application pages."""

    platform = Platform.WORKDAY

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the Workday platform."""
        if job.platform == Platform.WORKDAY:
            return True
        return detect_platform(job.url) == Platform.WORKDAY


__all__ = ["WorkdayAdapter"]
