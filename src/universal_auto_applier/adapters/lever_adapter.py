"""Lever adapter — untrusted, fixture-backed.

Per ``ROADMAP.md`` Phase 7, this adapter handles Lever-hosted
application pages. It is **untrusted**: it never submits, and always
pauses at ``review_before_submit``.

Supported URL patterns (from ``DATA_CONTRACTS.md``):

    jobs.lever.co
    *.lever.co

Navigation behavior:
- Lever job pages have an "Apply for this job" button that opens an
  inline application form. The adapter uses the shared
  :func:`observe_html` and :func:`safe_explore` infrastructure to find
  the apply button and reach the form.

Login behavior:
- Lever applications usually do not require login. If a login page is
  detected, the adapter stops and creates a ``login_required``
  intervention.

Form handling strategy:
- Uses the shared :func:`extract_form_fields` and :func:`fill_form`.
- Common Lever form fields: full name, email, phone, resume (file),
  and custom questions.
- File inputs are mapped to ``job.cv_pdf`` and ``job.cover_letter_pdf``.

Submit safety:
- ``submit_or_pause`` ALWAYS returns ``review_ready``. The adapter
  never clicks the "Submit application" button.

Failure behavior:
- If the page layout changes, the adapter fails safely with a
  structured ``AdapterResult``.

Test fixtures:
- ``tests/fixtures/platforms/lever_job.html`` (job page)
- ``tests/fixtures/platforms/lever_apply.html`` (apply form)
- ``tests/fixtures/platforms/lever_login.html`` (login page)
- ``tests/fixtures/platforms/lever_review.html`` (review page)
- ``tests/fixtures/platforms/lever_changed_layout.html`` (changed layout)

Known limitations:
- Does not handle Lever's "resume parsing" flow.
- Does not handle multi-page Lever applications.
- Does not bypass login or captcha. Both stop the adapter.
"""

from __future__ import annotations

from universal_auto_applier.adapters._ats_base import _UntrustedATSAdapter
from universal_auto_applier.adapters.registry import detect_platform
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform


class LeverAdapter(_UntrustedATSAdapter):
    """Untrusted adapter for Lever-hosted application pages."""

    platform = Platform.LEVER

    def can_handle(self, job: ApplicationJob) -> bool:
        """Return True if the job URL maps to the Lever platform."""
        if job.platform == Platform.LEVER:
            return True
        return detect_platform(job.url) == Platform.LEVER


__all__ = ["LeverAdapter"]
