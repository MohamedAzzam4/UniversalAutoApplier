"""Greenhouse adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` Phase 7, this adapter handles Greenhouse-hosted
application pages. It is **untrusted**: it never submits, and always
pauses at ``review_before_submit``.

Supported URL patterns (from ``DATA_CONTRACTS.md``):

    boards.greenhouse.io
    greenhouse.io
    *.greenhouse.io

Navigation behavior:
- Greenhouse job pages typically have an "Apply now" link that points
  to ``/apply`` or a separate application form page.
- The adapter uses the shared :func:`observe_html` and
  :func:`safe_explore` infrastructure to find the apply button and
  reach the form. It does not encode Greenhouse-specific selectors.
- If the page is a form already, the adapter proceeds to fill.

Login behavior:
- Greenhouse applications usually do not require login. If a login
  page is detected, the adapter stops and creates a
  ``login_required`` intervention.

Form handling strategy:
- Uses the shared :func:`extract_form_fields` and :func:`fill_form`.
- Common Greenhouse form fields: first name, last name, email, phone,
  resume (file), cover letter (file), and custom questions.
- File inputs are mapped to ``job.cv_pdf`` and ``job.cover_letter_pdf``.
- Custom questions without a known mapping become interventions.

Submit safety:
- ``submit_or_pause`` ALWAYS returns ``review_ready``. The adapter
  never clicks the "Submit application" button.

Failure behavior:
- If the page layout changes (no form fields found, expected selectors
  missing), the adapter returns ``AdapterResult.failed`` with
  ``reason='no_form_fields'`` or ``reason='observe_error'``.

Test fixtures:
- ``tests/fixtures/platforms/greenhouse_job.html`` (job page)
- ``tests/fixtures/platforms/greenhouse_apply.html`` (apply form)
- ``tests/fixtures/platforms/greenhouse_login.html`` (login page)
- ``tests/fixtures/platforms/greenhouse_review.html`` (review page)
- ``tests/fixtures/platforms/greenhouse_changed_layout.html`` (changed layout)

Known limitations:
- Does not handle Greenhouse's "resume parsing" flow (where uploading a
  resume auto-fills name/email). The shared fill engine fills fields
  deterministically from the candidate profile.
- Does not handle multi-page Greenhouse applications (where clicking
  "Next" advances to a second page). The fixture-based tests are
  single-page only.
- Does not bypass login or captcha. Both stop the adapter.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class GreenhouseAdapter(_UntrustedATSAdapter):
    """Untrusted adapter for Greenhouse-hosted application pages."""

    platform = Platform.GREENHOUSE

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the Greenhouse platform.

        Uses :func:`detect_platform` for deterministic hostname matching.
        Also returns True if the job's ``platform`` field is explicitly
        set to ``greenhouse``.
        """
        if job.platform == Platform.GREENHOUSE:
            return True
        return detect_platform(job.url) == Platform.GREENHOUSE


__all__ = ["GreenhouseAdapter"]
