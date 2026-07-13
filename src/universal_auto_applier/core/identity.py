"""Canonical URL normalization and deterministic ``application_id`` generation.

Per ``DATA_CONTRACTS.md`` -> ``ApplicationJob`` validation rules:

- ``application_id`` is ``sha256(identity_source).hexdigest()`` in lowercase.
- If both ``platform`` and ``external_job_id`` exist, ``identity_source`` is
  ``platform + ":" + external_job_id.strip()``.
- Otherwise, ``identity_source`` is the canonical URL.
- Canonical URL construction:
  - lowercases scheme and host
  - removes the fragment
  - removes the default port (80 for http, 443 for https)
  - removes a trailing slash except at the host root
  - removes query keys beginning with ``utm_``
  - removes these case-insensitive query keys: ``gclid``, ``fbclid``,
    ``mc_cid``, ``mc_eid``, ``ref``, ``refid``, ``trackingid``
  - preserves all other query keys and values
  - sorts remaining query parameters by key and then value

JobHunter and UniversalAutoApplier must share golden contract cases for this
algorithm (see ``tests/unit/test_canonical_url.py``).
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query keys to strip, case-insensitive. Any key starting with ``utm_`` is
# also stripped.
_TRACKING_QUERY_KEYS: frozenset[str] = frozenset(
    {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid"}
)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def canonicalize_url(url: str) -> str:
    """Return the canonical form of ``url``.

    The algorithm is specified in ``DATA_CONTRACTS.md`` and must be
    identical between JobHunter and UniversalAutoApplier.

    Args:
        url: An HTTP or HTTPS URL.

    Returns:
        The canonical URL string.

    Raises:
        ValueError: If ``url`` is not an HTTP or HTTPS URL.
    """
    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"URL must be HTTP or HTTPS, got scheme {scheme!r}")

    host = parts.hostname or ""
    host = host.lower()

    # Remove default port.
    port = parts.port
    netloc = host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    # Preserve userinfo if present (rare for job URLs, but be correct).
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo = f"{userinfo}:{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    # Path: remove trailing slash except at host root.
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Query: drop tracking keys, sort by (key, value).
    if parts.query:
        raw_pairs = parse_qsl(parts.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in raw_pairs if not _is_tracking_key(k)]
        filtered.sort(key=lambda pair: (pair[0], pair[1]))
        query = urlencode(filtered)
    else:
        query = ""

    # Fragment is always dropped.
    fragment = ""

    return urlunsplit((scheme, netloc, path, query, fragment))


def _is_tracking_key(key: str) -> bool:
    """Return True if ``key`` is a tracking query parameter."""
    lower = key.lower()
    if lower.startswith("utm_"):
        return True
    return lower in _TRACKING_QUERY_KEYS


def compute_application_id(
    *,
    platform: str | None,
    external_job_id: str | None,
    url: str,
) -> str:
    """Return the deterministic ``application_id`` for a job.

    Args:
        platform: The platform identifier (e.g. "greenhouse"), or None.
        external_job_id: The source-specific job ID, or None.
        url: The job URL (will be canonicalized).

    Returns:
        A lowercase SHA-256 hexdigest string.

    The identity source is ``platform + ":" + external_job_id.strip()`` if
    both ``platform`` and ``external_job_id`` are non-empty. Otherwise, the
    identity source is the canonical URL.
    """
    if platform and external_job_id and external_job_id.strip():
        identity_source = f"{platform}:{external_job_id.strip()}"
    else:
        identity_source = canonicalize_url(url)
    return hashlib.sha256(identity_source.encode("utf-8")).hexdigest()
