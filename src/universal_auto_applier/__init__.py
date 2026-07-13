"""UniversalAutoApplier - local-first generalized job application system.

This package owns queue import, application state, adapter routing, generic
navigation, generic form filling, interventions, answer memory,
review-before-submit, evidence, application history, and the operational
dashboard.

Version 1 runs locally on the user's machine. The dashboard binds to
127.0.0.1 by default. See ``docs/generalization/`` for the full planning pack.
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.0.1"
