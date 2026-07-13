"""UI layer.

Per ``TECHNICAL_BASELINE.md``:

* Version 1 uses semantic HTML, CSS, and modular browser JavaScript served by
  FastAPI.
* No frontend framework, no Node build pipeline.
* The UI must remain usable at 1280x720, 1440x900, and 390x844 viewports.
* Follow ``UI_UX_SPEC.md``; do not build a marketing or landing page.

The dashboard shell here is intentionally minimal: it proves the bootstrap
technical verification gate (``TECHNICAL_BASELINE.md`` point 3: "The dashboard
opens at the printed localhost URL") and exposes the health endpoint so the
user can see the system is alive. Real views (Queue, Interventions, History,
Job Detail, Logs, Settings) land in Phase 6.
"""

from __future__ import annotations
