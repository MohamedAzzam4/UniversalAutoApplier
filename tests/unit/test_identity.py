"""Unit tests for :mod:`universal_auto_applier.core.identity`.

These are the golden contract cases for canonical URL normalization and
deterministic ``application_id`` generation. Per ``DATA_CONTRACTS.md``,
JobHunter and UniversalAutoApplier must share these exact algorithms.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from universal_auto_applier.core.identity import canonicalize_url, compute_application_id

# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------


class TestCanonicalizeUrl:
    def test_lowercases_scheme_and_host(self) -> None:
        assert canonicalize_url("HTTPS://EXAMPLE.COM/path") == "https://example.com/path"

    def test_removes_fragment(self) -> None:
        assert canonicalize_url("https://example.com/path#section") == "https://example.com/path"

    def test_removes_default_port_http(self) -> None:
        assert canonicalize_url("http://example.com:80/path") == "http://example.com/path"

    def test_removes_default_port_https(self) -> None:
        assert canonicalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_keeps_non_default_port(self) -> None:
        assert canonicalize_url("https://example.com:8443/path") == "https://example.com:8443/path"

    def test_removes_trailing_slash(self) -> None:
        assert canonicalize_url("https://example.com/path/") == "https://example.com/path"

    def test_preserves_trailing_slash_at_host_root(self) -> None:
        assert canonicalize_url("https://example.com/") == "https://example.com/"

    def test_removes_utm_query_keys(self) -> None:
        result = canonicalize_url(
            "https://example.com/jobs?utm_source=google&utm_medium=cpc&keep=1"
        )
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "keep=1" in result

    def test_removes_tracking_query_keys(self) -> None:
        url = "https://example.com/jobs?gclid=abc&fbclid=def&mc_cid=ghi&mc_eid=jkl&ref=lnk&refid=x&trackingid=y&keep=1"
        result = canonicalize_url(url)
        for key in ("gclid", "fbclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid"):
            assert key not in result
        assert "keep=1" in result

    def test_tracking_keys_case_insensitive(self) -> None:
        result = canonicalize_url("https://example.com/jobs?GCLID=abc&UTM_Source=x&keep=1")
        assert "GCLID" not in result
        assert "UTM_Source" not in result
        assert "keep=1" in result

    def test_sorts_query_keys_by_key_then_value(self) -> None:
        url = "https://example.com/jobs?b=2&a=1&c=3"
        result = canonicalize_url(url)
        # parse the query to verify sorting
        query = result.split("?", 1)[1] if "?" in result else ""
        assert query == "a=1&b=2&c=3"

    def test_preserves_blank_query_values(self) -> None:
        result = canonicalize_url("https://example.com/jobs?empty=&keep=1")
        assert "empty=" in result
        assert "keep=1" in result

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            canonicalize_url("ftp://example.com/path")

    def test_rejects_javascript_scheme(self) -> None:
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            canonicalize_url("javascript:alert(1)")

    def test_idempotent(self) -> None:
        """Canonicalizing a canonical URL produces the same URL."""
        url = "https://example.com/jobs/123?utm_source=google&keep=1&ref=lnk"
        once = canonicalize_url(url)
        twice = canonicalize_url(once)
        assert once == twice

    def test_preserves_userinfo(self) -> None:
        # Rare for job URLs, but be correct.
        result = canonicalize_url("https://user:pass@example.com/path")
        assert "user:pass@example.com" in result

    def test_no_query(self) -> None:
        assert canonicalize_url("https://example.com/path") == "https://example.com/path"


# ---------------------------------------------------------------------------
# compute_application_id
# ---------------------------------------------------------------------------


class TestComputeApplicationId:
    def test_uses_platform_and_external_job_id_when_both_present(self) -> None:
        result = compute_application_id(
            platform="greenhouse", external_job_id="job-123", url="https://example.com/jobs/123"
        )
        expected = hashlib.sha256(b"greenhouse:job-123").hexdigest()
        assert result == expected

    def test_uses_canonical_url_when_no_external_job_id(self) -> None:
        result = compute_application_id(
            platform="greenhouse", external_job_id=None, url="https://Example.com/jobs/123/"
        )
        canonical = canonicalize_url("https://Example.com/jobs/123/")
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        assert result == expected

    def test_uses_canonical_url_when_no_platform(self) -> None:
        result = compute_application_id(
            platform=None, external_job_id="job-123", url="https://example.com/jobs/123"
        )
        canonical = canonicalize_url("https://example.com/jobs/123")
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        assert result == expected

    def test_strips_external_job_id_whitespace(self) -> None:
        result = compute_application_id(
            platform="greenhouse", external_job_id="  job-123  ", url="https://example.com/jobs/123"
        )
        expected = hashlib.sha256(b"greenhouse:job-123").hexdigest()
        assert result == expected

    def test_empty_external_job_id_falls_back_to_url(self) -> None:
        result = compute_application_id(
            platform="greenhouse", external_job_id="   ", url="https://example.com/jobs/123"
        )
        canonical = canonicalize_url("https://example.com/jobs/123")
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        assert result == expected

    def test_empty_platform_falls_back_to_url(self) -> None:
        result = compute_application_id(
            platform="", external_job_id="job-123", url="https://example.com/jobs/123"
        )
        canonical = canonicalize_url("https://example.com/jobs/123")
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        assert result == expected

    def test_returns_lowercase_hexdigest(self) -> None:
        result = compute_application_id(
            platform="greenhouse", external_job_id="job-123", url="https://example.com/jobs/123"
        )
        assert result == result.lower()
        assert re.fullmatch(r"[0-9a-f]{64}", result)

    def test_deterministic(self) -> None:
        """Same inputs always produce the same output."""
        kwargs = dict(
            platform="greenhouse", external_job_id="job-123", url="https://example.com/jobs/123"
        )
        assert compute_application_id(**kwargs) == compute_application_id(**kwargs)

    def test_different_urls_produce_different_ids(self) -> None:
        id1 = compute_application_id(
            platform=None, external_job_id=None, url="https://example.com/jobs/1"
        )
        id2 = compute_application_id(
            platform=None, external_job_id=None, url="https://example.com/jobs/2"
        )
        assert id1 != id2

    def test_tracking_params_do_not_affect_id(self) -> None:
        """URLs differing only in tracking params produce the same ID."""
        id1 = compute_application_id(
            platform=None,
            external_job_id=None,
            url="https://example.com/jobs/123?utm_source=google",
        )
        id2 = compute_application_id(
            platform=None, external_job_id=None, url="https://example.com/jobs/123?ref=linkedin"
        )
        id3 = compute_application_id(
            platform=None, external_job_id=None, url="https://example.com/jobs/123"
        )
        assert id1 == id2 == id3
