"""LinkedIn Easy Apply adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` Phase 7, this adapter handles LinkedIn Easy Apply
application flows. It is **untrusted**: it never submits, and always
pauses at ``review_before_submit``.

Supported URL patterns (from ``DATA_CONTRACTS.md``):

    linkedin.com/jobs
    *.linkedin.com/jobs

Navigation behavior:
- LinkedIn job pages have an "Easy Apply" button that opens a multi-step
  application modal. The adapter uses the shared :func:`observe_html`
  and :func:`safe_explore` infrastructure to find the Easy Apply button
  and reach the form.

Login behavior:
- LinkedIn **always** requires login. If a login page is detected (URL
  contains ``/login`` or page text contains "Sign in"), the adapter
  stops and creates a ``login_required`` intervention.
- The adapter never submits credentials and never bypasses login.
- This is the strictest of the five ATS adapters because LinkedIn's
  anti-automation measures make any browser interaction without login
  unreliable and unsafe.

Form handling strategy:
- Uses the shared :func:`extract_form_fields` and :func:`fill_form`.
- LinkedIn Easy Apply forms typically include: name, email, phone,
  resume (file), and screening questions (work authorization, years of
  experience, etc.).
- File inputs are mapped to ``job.cv_pdf``.
- Screening questions without a known mapping become interventions.

Submit safety:
- ``submit_or_pause`` ALWAYS returns ``review_ready``. The adapter
  never clicks the "Submit application" button.

Failure behavior:
- If the page layout changes (LinkedIn frequently updates their DOM),
  the adapter fails safely with a structured ``AdapterResult``.

Test fixtures:
- ``tests/fixtures/platforms/linkedin_job.html`` (job page with Easy Apply)
- ``tests/fixtures/platforms/linkedin_apply.html`` (Easy Apply form)
- ``tests/fixtures/platforms/linkedin_login.html`` (login page)
- ``tests/fixtures/platforms/linkedin_review.html`` (review page)
- ``tests/fixtures/platforms/linkedin_changed_layout.html`` (changed layout)

Known limitations:
- Does not handle LinkedIn's multi-step Easy Apply modal (Next/Review
  buttons across multiple steps). Fixture tests are single-page.
- Does not handle LinkedIn's SSO or captcha. Both stop the adapter.
- Does not bypass login. The adapter stops on the first login wall.
- Does not store or submit LinkedIn credentials.
- Does not handle LinkedIn's "profile autofill" feature where the
  candidate's LinkedIn profile pre-fills the form.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class LinkedInEasyApplyAdapter(_UntrustedATSAdapter):
    """Untrusted adapter for LinkedIn Easy Apply flows."""

    platform = Platform.LINKEDIN_EASY_APPLY

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the LinkedIn Easy Apply platform."""
        if job.platform == Platform.LINKEDIN_EASY_APPLY:
            return True
        return detect_platform(job.url) == Platform.LINKEDIN_EASY_APPLY


__all__ = ["LinkedInEasyApplyAdapter"]
