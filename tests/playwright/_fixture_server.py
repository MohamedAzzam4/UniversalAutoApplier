"""Shared fixture-server creation with HTTP readiness verification.

Every Playwright test file that serves static fixtures used this
vulnerable pattern::

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield url   # <-- no readiness check!

The server thread might not have called ``accept()`` yet when Playwright
fires ``page.goto()``, producing ``net::ERR_CONNECTION_FAILED``.  The one
module-level helper below eliminates that race.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    """HTTP handler that suppresses log output during tests."""

    def log_message(self, _format: str, *args: object) -> None:
        del args


def serve_fixture_dir(fixture_dir: Path) -> Iterator[str]:
    """Start a ``ThreadingHTTPServer`` serving ``fixture_dir``, verify HTTP
    readiness, yield its base URL, then shut down reliably.

    Raises
    ------
    RuntimeError
        If the server does not respond to an HTTP GET within 5 seconds.
    AssertionError
        If the server thread dies before the fixture yields.
    """
    handler = partial(_QuietHandler, directory=str(fixture_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address
    url = f"http://{host}:{port}"

    deadline = time.time() + 5.0
    ready = False
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/conditional_reveal.html", timeout=1.0) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(0.1)

    if not ready:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        raise RuntimeError(f"Fixture server at {url} failed to become ready in 5s: {last_error}")

    assert thread.is_alive(), f"Fixture server thread died before yielding URL {url}"

    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
