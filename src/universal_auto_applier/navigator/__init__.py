"""navigator package.

Phase 3 implements:
- :class:`ClickableClassifier` — deterministic button classification
- :class:`PageObserver` — extracts page state from the DOM
- :class:`SafeExplorer` — safe exploration loop that never clicks submit

See ``docs/generalization/ROADMAP.md`` Phase 3 for details.
"""

from __future__ import annotations

from universal_auto_applier.navigator.clickable_classifier import (
    ClassificationResult,
    classify_clickable,
)
from universal_auto_applier.navigator.page_observer import observe_html
from universal_auto_applier.navigator.safe_explorer import (
    DEFAULT_MAX_STEPS,
    ExplorationResult,
    ExplorationStep,
    safe_explore,
)

__all__ = [
    "ClassificationResult",
    "classify_clickable",
    "observe_html",
    "safe_explore",
    "ExplorationResult",
    "ExplorationStep",
    "DEFAULT_MAX_STEPS",
]
