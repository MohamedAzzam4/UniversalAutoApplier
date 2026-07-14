"""form_engine package.

Phase 4 implements:
- :func:`extract_form_fields` — extract form fields from HTML
- :func:`map_field` / :func:`map_fields` — deterministic field-to-value mapping
- :func:`fill_form` — fill form fields safely, never submit

See ``docs/generalization/ROADMAP.md`` Phase 4 for details.
"""

from __future__ import annotations

from universal_auto_applier.form_engine.field_mapper import (
    CONFIDENCE_THRESHOLD,
    map_field,
    map_fields,
)
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.form_engine.live_executor import LiveFormExecution, execute_live_form
from universal_auto_applier.form_engine.schema_extractor import extract_form_fields

__all__ = [
    "extract_form_fields",
    "map_field",
    "map_fields",
    "fill_form",
    "CONFIDENCE_THRESHOLD",
    "LiveFormExecution",
    "execute_live_form",
]
