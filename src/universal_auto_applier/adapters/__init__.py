"""adapters package.

Phase 2 implements:
- :class:`ApplicationAdapter` — base class / protocol for all adapters
- :class:`AdapterRegistry` — deterministic adapter selection
- :class:`GenericAdapter` — fallback for unknown platforms
- :class:`SiemensAdapter` — narrow boundary to SiemensAutoApplier

Phase 3+ will add:
- :class:`GreenhouseAdapter`, :class:`LeverAdapter`, etc.

See ``docs/generalization/ROADMAP.md`` for the full phase plan.
"""

from __future__ import annotations

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.adapters.generic_adapter import GenericAdapter
from universal_auto_applier.adapters.registry import (
    AdapterRegistry,
    NoAdapterError,
    detect_platform,
)
from universal_auto_applier.adapters.siemens_adapter import (
    SiemensAdapter,
    SiemensAdapterConfig,
)

__all__ = [
    "ApplicationAdapter",
    "AdapterRegistry",
    "NoAdapterError",
    "GenericAdapter",
    "SiemensAdapter",
    "SiemensAdapterConfig",
    "detect_platform",
]
