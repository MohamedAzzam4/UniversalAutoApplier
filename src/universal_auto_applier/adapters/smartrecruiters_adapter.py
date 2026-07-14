"""SmartRecruiters adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` Phase 7, this adapter handles SmartRecruiters-hosted
application pages. It is **untrusted**: it never submits, and always
pauses at ``review_before_submit``.

Supported URL patterns (from ``DATA_CONTRACTS.md``):

    smartrecruiters.com
    *.smartrecruiters.com
    careers.smartrecruiters.com

Navigation behavior:
- SmartRecruiters job pages have an "Apply now" button that opens a
  multi-step application modal or page. The adapter uses the shared
  :func:`observe_html` and :func:`safe_explore` infrastructure to find
  the apply button and reach the form.

Login behavior:
- SmartRecruiters applications may require login (especially for
  returning candidates). If a login page is detected, the adapter
  stops and creates a ``login_required`` intervention.
- The adapter never submits credentials and never bypasses login.

Form handling strategy:
- Uses the shared :func:`extract_form_fields` and :func:`fill_form`.
- Common SmartRecruiters form fields: first name, last name, email,
  phone, resume (file), cover letter (file), and screening questions.
- File inputs are mapped to ``job.cv_pdf`` and ``job.cover_letter_pdf``.
- Screening questions without a known mapping become interventions.

Submit safety:
- ``submit_or_pause`` ALWAYS returns ``review_ready``. The adapter
  never clicks the "Submit application" button.

Failure behavior:
- If the page layout changes, the adapter fails safely with a
  structured ``AdapterResult``.

Test fixtures:
- ``tests/fixtures/platforms/smartrecruiters_job.html`` (job page)
- ``tests/fixtures/platforms/smartrecruiters_apply.html`` (apply form)
- ``tests/fixtures/platforms/smartrecruiters_login.html`` (login page)
- ``tests/fixtures/platforms/smartrecruiters_review.html`` (review page)
- ``tests/fixtures/platforms/smartrecruiters_changed_layout.html`` (changed layout)

Known limitations:
- Does not handle SmartRecruiters's multi-step application flow.
- Does not handle SSO login redirects.
- Does not bypass login or captcha. Both stop the adapter.
- Does not store or submit credentials.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class SmartRecruitersAdapter(_UntrustedATSAdapter):
    """Untrusted adapter for SmartRecruiters-hosted application pages."""

    platform = Platform.SMARTRECRUITERS

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the SmartRecruiters platform."""
        if job.platform == Platform.SMARTRECRUITERS:
            return True
        return detect_platform(job.url) == Platform.SMARTRECRUITERS


__all__ = ["SmartRecruitersAdapter"]
