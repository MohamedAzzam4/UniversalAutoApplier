"""Adapter registry with deterministic selection.

Per ``ROADMAP.md`` WP 2.2:

- Register adapters in a deterministic order.
- Select the first known adapter that returns ``can_handle(job) == True``.
- Fail startup if two known adapters have the same routing priority and
  both claim the same platform fixture.
- Fall back to :class:`GenericAdapter` only when no known adapter matches.

Platform detection (from ``DATA_CONTRACTS.md``):

    jobs.siemens.com             -> siemens
    greenhouse.io, boards.greenhouse.io -> greenhouse
    jobs.lever.co                -> lever
    myworkdayjobs.com            -> workday
    smartrecruiters.com          -> smartrecruiters
    linkedin.com/jobs            -> linkedin_easy_apply or unknown

The registry uses URL hostname matching for deterministic routing.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from universal_auto_applier.adapters.base import ApplicationAdapter
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import Platform

# Hostname patterns for platform detection.
# Keys are lowercased hostname suffixes; values are the Platform enum.
_PLATFORM_HOST_PATTERNS: dict[str, Platform] = {
    "jobs.siemens.com": Platform.SIEMENS,
    "greenhouse.io": Platform.GREENHOUSE,
    "boards.greenhouse.io": Platform.GREENHOUSE,
    "jobs.lever.co": Platform.LEVER,
    "myworkdayjobs.com": Platform.WORKDAY,
    "smartrecruiters.com": Platform.SMARTRECRUITERS,
    "linkedin.com/jobs": Platform.LINKEDIN_EASY_APPLY,
}


def detect_platform(url: str) -> Platform:
    """Detect the platform from a job URL's hostname.

    This is a deterministic heuristic based on the URL's hostname. It does
    not inspect page content. If no known pattern matches, returns
    :attr:`Platform.UNKNOWN`.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or ""

    # Check exact host matches first.
    for pattern, platform in _PLATFORM_HOST_PATTERNS.items():
        if "/" in pattern:
            # Pattern includes a path component (e.g. linkedin.com/jobs).
            host_part, path_part = pattern.split("/", 1)
            if host == host_part or host.endswith("." + host_part):
                if path.startswith("/" + path_part) or path.startswith(path_part):
                    return platform
        else:
            if host == pattern or host.endswith("." + pattern):
                return platform

    return Platform.UNKNOWN


class AdapterRegistry:
    """Registry of adapters with deterministic selection.

    Adapters are registered in order and selected by the first one that
    returns ``can_handle(job) == True``. The registry enforces that no two
    adapters claim the same ``platform`` value (to prevent routing ambiguity).
    """

    def __init__(self) -> None:
        self._adapters: list[ApplicationAdapter] = []
        self._platforms_seen: set[Platform] = set()

    def register(self, adapter: ApplicationAdapter) -> None:
        """Register ``adapter``.

        Raises:
            ValueError: If an adapter with the same ``platform`` is already
                registered.
        """
        if adapter.platform in self._platforms_seen:
            raise ValueError(
                f"Duplicate adapter for platform {adapter.platform!r}: "
                f"{adapter.__class__.__name__} conflicts with an existing adapter"
            )
        self._platforms_seen.add(adapter.platform)
        self._adapters.append(adapter)

    def select(self, job: ApplicationJob) -> ApplicationAdapter:
        """Return the first adapter that can handle ``job``.

        If no registered adapter returns ``can_handle(job) == True``, returns
        the generic fallback adapter (if registered) or raises
        :class:`NoAdapterError`.
        """
        for adapter in self._adapters:
            if adapter.can_handle(job):
                return adapter
        raise NoAdapterError(
            f"No adapter can handle job {job.application_id[:12]} "
            f"(platform={job.platform}, url={job.url})"
        )

    def select_by_platform(self, platform: Platform) -> ApplicationAdapter:
        """Return the adapter registered for ``platform``.

        Raises:
            NoAdapterError: If no adapter is registered for ``platform``.
        """
        for adapter in self._adapters:
            if adapter.platform == platform:
                return adapter
        raise NoAdapterError(f"No adapter registered for platform {platform!r}")

    @property
    def adapters(self) -> list[ApplicationAdapter]:
        """Return a copy of the registered adapters list."""
        return list(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)


class NoAdapterError(Exception):
    """Raised when no adapter can handle a job."""


__all__ = ["AdapterRegistry", "NoAdapterError", "detect_platform"]
