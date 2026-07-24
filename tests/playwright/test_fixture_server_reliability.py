"""Regression tests for fixture-server reliability.

Verifies:
1. The server is reachable via HTTP before the fixture yields.
2. Sequential fixture-server instances do not interfere.
3. A genuine navigation failure produces a direct diagnostic
   (not swallowed by a runner).
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.playwright._fixture_server import serve_fixture_dir

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


class TestFixtureServerReachability:
    """The server must be reachable via HTTP before the fixture yields."""

    def test_server_reachable_via_http(self) -> None:
        """Fetch ``/conditional_reveal.html`` immediately after the
        ``serve_fixture_dir`` generator yields — must succeed."""
        gen = serve_fixture_dir(FIXTURE_DIR)
        url = next(gen)
        try:
            with urllib.request.urlopen(f"{url}/conditional_reveal.html", timeout=2.0) as resp:
                assert resp.status == 200, f"Expected 200, got {resp.status}"
        finally:
            gen.close()


class TestFixtureServerSequentialIsolation:
    """Sequential fixture-server instances must not interfere."""

    def test_sequential_instances_do_not_interfere(self) -> None:
        """Start a server, use it, tear it down, then start another.
        The second server must work on a different port."""
        gen1 = serve_fixture_dir(FIXTURE_DIR)
        url1 = next(gen1)
        with urllib.request.urlopen(f"{url1}/", timeout=2.0) as resp:
            assert resp.status == 200, f"First server: expected 200, got {resp.status}"
        gen1.close()

        gen2 = serve_fixture_dir(FIXTURE_DIR)
        url2 = next(gen2)
        try:
            assert url2 != url1, (
                f"Second server must use a different port: first={url1}, second={url2}"
            )
            with urllib.request.urlopen(f"{url2}/", timeout=2.0) as resp:
                assert resp.status == 200, f"Second server: expected 200, got {resp.status}"
        finally:
            gen2.close()


class TestNavigationFailureDiagnostic:
    """Connecting to a non-existent server must produce a clear error."""

    def test_unreachable_port_raises_connection_error(self) -> None:
        """Try to HTTP-GET a port with no server. Must raise an exception
        (connection refused / timeout) rather than silently returning an
        empty or bogus 200 response."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        url = f"http://127.0.0.1:{port}/"
        # On Windows, connecting to a closed port that is in TIME_WAIT can
        # either raise URLError (connection refused) or timeout. Either is
        # a valid diagnostic — the key assertion is that *some* exception
        # is raised, not that the response is an HTTP 200.
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(url, timeout=2.0)
