"""Pytest configuration shared by all test types.

Per ``docs/generalization/TESTING_STRATEGY.md``:

* Tests must not depend on execution order or a developer's real local database.
* Use a fresh temporary SQLite database for tests that need persistence.
* Use fake candidate data; do not call live ATS websites.

The fixtures here give every test a clean, isolated environment without
touching the developer's local ``.uaa_data`` directory.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Ensure ``src/`` is importable when running ``python -m pytest`` without an
# editable install. This is the only place we touch sys.path; runtime code
# must not.
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Tests must never write to the developer's real data directory.
os.environ.setdefault("UAA_DATA_DIR", str(REPO_ROOT / ".uaa_data_test"))
os.environ.setdefault("UAA_HOST", "127.0.0.1")
os.environ.setdefault("UAA_PORT", "8001")
os.environ.setdefault("UAA_SUBMIT_MODE", "review")
os.environ.setdefault("UAA_BROWSER_HEADLESS", "true")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:  # noqa: F821 - forward ref
    """Return a :class:`Settings` instance pointing at a temp data dir."""
    from universal_auto_applier.config import Settings

    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        jobhunter_queue=None,
        siemens_repo=None,
        browser_headless=True,
        submit_mode="review",
    )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:  # noqa: F821
    """Yield a SQLAlchemy engine bound to a fresh temp SQLite database.

    Uses NullPool so connections are closed immediately when sessions close.
    This avoids ResourceWarning: unclosed database on Python 3.14, where
    sqlite3 is stricter about finalizing connections.

    The PRAGMA foreign_keys=ON event listener is the same as in
    :func:`universal_auto_applier.persistence.db.make_engine`.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import NullPool

    from universal_auto_applier.persistence.db import build_engine_url
    from universal_auto_applier.persistence.models import Base

    db_path = tmp_path / "test_uaa.sqlite"
    engine = create_engine(
        build_engine_url(db_path),
        future=True,
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated_db_url(tmp_path: Path) -> str:
    """Apply all migrations to a fresh SQLite DB and return its URL."""
    from universal_auto_applier.persistence.db import build_engine_url
    from universal_auto_applier.persistence.migrations import apply_migrations

    db_path = tmp_path / "migrated_uaa.sqlite"
    url = build_engine_url(db_path)
    apply_migrations(url)
    return url


@pytest.fixture
def client(settings) -> Iterator[TestClient]:  # noqa: F821 - forward ref
    """Yield a FastAPI TestClient with a temp data directory."""
    from universal_auto_applier.api.app import create_app

    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_url(client: TestClient) -> str:
    """Base URL for the running TestClient; usable by Playwright page.goto()."""
    return str(client.base_url)


@pytest.fixture
def server_url(settings, tmp_path_factory) -> Iterator[str]:
    """Start a real uvicorn server on an ephemeral port and yield its base URL.

    This fixture is for Playwright tests, which need a real TCP socket. The
    TestClient-based ``client`` fixture uses a virtual ``http://testserver``
    hostname that a real browser cannot reach.
    """
    import socket
    import threading
    from contextlib import closing

    import uvicorn

    from universal_auto_applier.api.app import create_app

    # Pick a free port by opening a temporary socket.
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    # The data dir must exist before the lifespan starts the engine.
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    app = create_app(settings=settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        # The dashboard does not need websockets. Disabling ws avoids loading
        # the deprecated `websockets.legacy` module, which would raise a
        # DeprecationWarning under our strict pytest filterwarnings config.
        ws="none",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to accept connections (max ~5s).
    import time

    deadline = time.time() + 5.0
    base = f"http://127.0.0.1:{port}/"
    ready = False
    while time.time() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                ready = True
                break
        except OSError:
            time.sleep(0.1)

    if not ready:
        server.should_exit = True
        thread.join(timeout=2.0)
        raise RuntimeError("uvicorn server did not start in time")

    yield base

    server.should_exit = True
    thread.join(timeout=5.0)
