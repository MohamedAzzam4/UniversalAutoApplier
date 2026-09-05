"""Pre-preparation application-target freshness preflight.

Runs AFTER package/schema/document-lineage validation but BEFORE any
browser form preparation/filling. For ATS hosts with a deterministic
public lookup, the target is revalidated live:

- Workday (``*.myworkdayjobs.com``): the requisition ID embedded in the
  application URL (``..._ID12345``) is searched on the tenant/board CXS
  endpoint derived from the URL itself. A matching posting means LIVE;
  an accepted empty result means EXPIRED.
- SmartRecruiters (``jobs.smartrecruiters.com``): the public posting
  detail endpoint answers 200 when LIVE and 404 when EXPIRED.

Outcomes are ``LIVE`` / ``EXPIRED`` / ``UNKNOWN``. Only a positive
EXPIRED stops preparation (with reason ``APPLICATION_EXPIRED`` and NO
human handoff). Transport errors, unexpected payloads, and unsupported
hosts yield UNKNOWN, which never blocks preparation.

A secondary page-text signal covers Workday application URLs that carry
no requisition token: the actual Pilot-01 expired page renders the
equivalent of "Die Seite, die Sie suchen, ist nicht vorhanden". The CXS
ID lookup is always preferred when an ID is available.

This module is UAA-side only and deliberately duplicates a few lines of
URL parsing rather than coupling UAA back to JobHunter code.

Pure ``httpx`` + stdlib. No browser, no submission, no PII handling.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger("universal_auto_applier.supervisor.target_preflight")

LIVE = "LIVE"
EXPIRED = "EXPIRED"
UNKNOWN = "UNKNOWN"

FETCH_TIMEOUT = 15  # seconds

# Matches the trailing requisition token in Workday application URLs, e.g.
# ".../job/Nuremberg/Werkstudent-X--m-w-d-_ID15374" -> "ID15374".
_WORKDAY_ID_RE = re.compile(r"_(ID\d+)\s*$")

# Page-text markers of a removed Workday posting (Pilot-01 evidence:
# "Die Seite, die Sie suchen, ist nicht vorhanden"). Secondary signal
# only — the CXS ID lookup is authoritative whenever an ID is available.
WORKDAY_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "ist nicht vorhanden",
    "is no longer available",
    "no longer available",
    "position has been filled",
    "this job has expired",
    "no longer accepting applications",
)


def parse_workday_identity(url: str) -> tuple[str, str, str] | None:
    """Split a Workday application URL into (tenant, board, requisition_id).

    Returns None when the URL is not a recognizable Workday application
    URL. Host shape is ``{tenant}.wd{N}.myworkdayjobs.com``; path shape is
    ``/{locale}/{board}/job/{slug}_IDxxx``.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    labels = host[: -len(".myworkdayjobs.com")].split(".")
    if len(labels) != 2 or not labels[0] or not re.fullmatch(r"wd\d+", labels[1]):
        return None
    tenant = labels[0]
    segments = [seg for seg in parts.path.split("/") if seg]
    # Expected shape: {locale}/{board}/job/{slug}_IDxxx
    if len(segments) < 4 or segments[2].lower() != "job":
        return None
    board = segments[1]
    match = _WORKDAY_ID_RE.search(segments[-1])
    if not match:
        return tenant, board, ""
    return tenant, board, match.group(1)


def _page_looks_expired(html: str) -> bool:
    """Check page text against known removed-posting markers."""
    lowered = html.lower()
    return any(marker in lowered for marker in WORKDAY_NOT_FOUND_MARKERS)


def _fetch_page_text(url: str, timeout: float) -> str | None:
    """Fetch a page and return cleaned text, or None on any failure."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                url,
                headers={
                    "Accept": "text/html",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
            )
    except Exception:  # noqa: BLE001 — transient, never expiry
        return None
    if resp.status_code == 404:
        return ""
    if resp.status_code != 200:
        return None
    text = re.sub(r"<[^>]+>", " ", resp.text)
    return re.sub(r"\s+", " ", text).strip()


def check_workday(url: str, timeout: float = FETCH_TIMEOUT) -> tuple[str, str]:
    """Revalidate one Workday application URL.

    Preferred path: CXS requisition-ID lookup (deterministic). Fallback
    when the URL carries no ID token: page-text markers (Pilot-01
    evidence). Anything inconclusive is UNKNOWN.
    """
    identity = parse_workday_identity(url)
    if identity is None:
        return UNKNOWN, "unrecognized Workday application URL shape"
    tenant, board, requisition_id = identity

    if requisition_id:
        endpoint = f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    endpoint,
                    json={
                        "appliedFacets": {},
                        "limit": 5,
                        "offset": 0,
                        "searchText": requisition_id,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0",
                    },
                )
        except Exception as e:  # noqa: BLE001 — transient, never expiry
            return UNKNOWN, f"workday lookup transport failure: {type(e).__name__}"
        if resp.status_code != 200:
            return UNKNOWN, f"workday lookup HTTP {resp.status_code}"
        try:
            decoded: Any = resp.json()
        except ValueError:
            return UNKNOWN, "workday lookup returned non-JSON payload"
        # NOTE: cast() re-widens values after shape checks — pyright would
        # otherwise narrow JSON-decoded data into Unknown generics whose
        # member access it rejects.
        if type(decoded) is not dict:
            return UNKNOWN, "workday lookup returned unexpected payload shape"
        data: Any = cast(Any, decoded)
        postings: Any = data.get("jobPostings", [])
        if type(postings) is not list:
            return UNKNOWN, "workday lookup returned unexpected postings shape"
        items: list[Any] = cast("list[Any]", postings)
        token = f"_{requisition_id}"
        for posting in items:
            try:
                external_path = posting.get("externalPath", "")
            except AttributeError:
                continue
            if not isinstance(external_path, str):
                continue
            if token in external_path:
                return LIVE, f"requisition {requisition_id} present in tenant index"
        return EXPIRED, f"requisition {requisition_id} absent from tenant index"

    # No requisition token: fall back to page-text evidence.
    text = _fetch_page_text(url, timeout)
    if text is None:
        return UNKNOWN, "workday page fetch failed"
    if text == "" or _page_looks_expired(text):
        return EXPIRED, "workday page shows removed-posting markers"
    return UNKNOWN, "no requisition token and no expiry markers"


def check_smartrecruiters(url: str, timeout: float = FETCH_TIMEOUT) -> tuple[str, str]:
    """Revalidate one SmartRecruiters apply URL via the public detail API.

    HTTP 200 means LIVE, HTTP 404 means EXPIRED, anything else is UNKNOWN.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return UNKNOWN, "unparseable URL"
    host = (parts.hostname or "").lower()
    if host != "jobs.smartrecruiters.com":
        return UNKNOWN, "not a SmartRecruiters apply URL"
    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) < 2:
        return UNKNOWN, "SmartRecruiters URL missing company/posting segments"
    company, posting_slug = segments[0], segments[1]
    posting_id = posting_slug.split("-")[0]
    if not posting_id:
        return UNKNOWN, "SmartRecruiters URL missing posting id"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}",
                headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            )
    except Exception as e:  # noqa: BLE001 — transient, never expiry
        return UNKNOWN, f"smartrecruiters lookup transport failure: {type(e).__name__}"
    if resp.status_code == 200:
        return LIVE, f"posting {posting_id} present"
    if resp.status_code == 404:
        return EXPIRED, f"posting {posting_id} returns 404"
    return UNKNOWN, f"smartrecruiters lookup HTTP {resp.status_code}"


def check_target_freshness(url: str, platform: str = "") -> tuple[str, str]:
    """Revalidate one application target URL.

    Dispatches on host (platform is a hint only). Returns
    (LIVE|EXPIRED|UNKNOWN, sanitized detail). Hosts without a
    deterministic lookup yield UNKNOWN.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return UNKNOWN, "unparseable URL"
    _ = platform  # hint reserved for future dispatch
    if host.endswith(".myworkdayjobs.com"):
        return check_workday(url)
    if host == "jobs.smartrecruiters.com":
        return check_smartrecruiters(url)
    return UNKNOWN, "no deterministic lookup for this host"


__all__ = [
    "EXPIRED",
    "FETCH_TIMEOUT",
    "LIVE",
    "UNKNOWN",
    "WORKDAY_NOT_FOUND_MARKERS",
    "check_smartrecruiters",
    "check_target_freshness",
    "check_workday",
    "parse_workday_identity",
]
