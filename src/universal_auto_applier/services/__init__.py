"""Application services.

Per the architecture rules in ``TECHNICAL_BASELINE.md``:

    api/ui -> services -> core contracts
    services -> repositories and adapter interfaces

Services define transaction boundaries and orchestrate repository calls.
"""

from __future__ import annotations
