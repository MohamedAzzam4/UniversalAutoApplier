"""Cross-version controlled-submission harness tests.

These tests run a UAA API server + Playwright browser in a subprocess,
isolating the Playwright greenlet from pytest-playwright. They send real
HTTP requests to the subprocess server.

Architecture:
- Parent pytest process: starts subprocess, sends HTTP requests, verifies results.
- Subprocess: owns FastAPI app, SQLite DB, Playwright lifecycle, fixture server.
- No Playwright objects cross the process boundary.

These tests run on ALL Python versions (3.11, 3.12, 3.13, 3.14) without
any version skips. They are marked as `integration` (not `playwright` or
`live`) because they don't use the pytest-playwright plugin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"
HARNESS_SERVER = Path(__file__).parent.parent / "harness" / "submission_server.py"

pytestmark = pytest.mark.integration


class SubprocessServer:
    """Manages a subprocess UAA server with Playwright."""

    def __init__(
        self,
        data_dir: Path,
        fixture_dir: Path = FIXTURE_DIR,
        fixture_page: str = "harness_submit.html",
        enable_real_submission: bool = True,
    ) -> None:
        self._data_dir = data_dir
        self._fixture_dir = fixture_dir
        self._fixture_page = fixture_page
        self._enable = enable_real_submission
        self._proc: subprocess.Popen | None = None
        self.port: int = 0
        self.application_id: str = ""
        self.fixture_url: str = ""
        self.fixture_port: int = 0

    def start(self, timeout: float = 30.0) -> None:
        env = os.environ.copy()
        # Ensure src/ is on the path for the subprocess.
        repo_root = Path(__file__).resolve().parent.parent.parent
        env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")

        self._proc = subprocess.Popen(
            [
                sys.executable,
                str(HARNESS_SERVER),
                "--data-dir",
                str(self._data_dir),
                "--fixture-dir",
                str(self._fixture_dir),
                "--fixture-page",
                self._fixture_page,
                "--enable-real-submission",
                "true" if self._enable else "false",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )

        # Wait for the readiness signal.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                if self._proc.poll() is not None:
                    raise RuntimeError("Server exited early (stderr suppressed)")
                continue
            line = line.strip()
            if line.startswith("READY:"):
                info = json.loads(line[6:])
                self.port = info["port"]
                self.application_id = info["application_id"]
                self.fixture_url = info["fixture_url"]
                self.fixture_port = info["fixture_port"]
                # Wait for the server to start accepting connections.
                import socket as _socket

                deadline2 = time.monotonic() + 10.0
                while time.monotonic() < deadline2:
                    try:
                        with _socket.create_connection(("127.0.0.1", self.port), timeout=1):
                            return
                    except OSError:
                        time.sleep(0.2)
                raise RuntimeError("Server ready but port not accepting connections")

        # Timeout.
        self.stop()
        raise RuntimeError("Server did not become ready in time")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        finally:
            # Close stdout pipe to prevent ResourceWarning.
            if self._proc.stdout:
                self._proc.stdout.close()
            self._proc = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=120.0)

    def get_metrics(self) -> dict[str, Any]:
        with self.client() as c:
            resp = c.get("/api/harness/metrics")
            return resp.json()

    def get_db_path(self) -> Path:
        return self._data_dir / "uaa.sqlite"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _observe_and_approve(client: httpx.Client, app_id: str) -> str:
    """Call /observe to persist a snapshot, then get the approval_id from status."""
    resp = client.post(f"/api/submit/{app_id}/observe")
    assert resp.status_code == 200, f"observe failed: {resp.text}"
    resp3 = client.get(f"/api/submit/{app_id}/status")
    assert resp3.status_code == 200
    status = resp3.json()
    # New response format: {"snapshot": {"active_approval_id": "..."}}
    approval_id = status.get("snapshot", {}).get("active_approval_id")
    if not approval_id:
        # Fallback: old format
        approval_id = status.get("approval_id")
    if not approval_id:
        raise RuntimeError(f"No approval_id found after observe. Status: {status}")
    return approval_id


def _query_db_counts(db_path: Path) -> dict[str, int]:
    """Query the SQLite DB for claim and result counts."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM submission_claims")
        claim_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM submission_results")
        result_count = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        claim_count = 0
        result_count = 0
    finally:
        conn.close()
    return {"claims": claim_count, "results": result_count}


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: Path) -> SubprocessServer:
    """Start a subprocess server for each test."""
    srv = SubprocessServer(
        data_dir=tmp_path / "uaa_harness",
        fixture_dir=FIXTURE_DIR,
        fixture_page="harness_submit.html",
        enable_real_submission=True,
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def server_no_submit(tmp_path: Path) -> SubprocessServer:
    """Server with real submission disabled."""
    srv = SubprocessServer(
        data_dir=tmp_path / "uaa_harness_disabled",
        fixture_dir=FIXTURE_DIR,
        fixture_page="harness_submit.html",
        enable_real_submission=False,
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def server_no_outcome(tmp_path: Path) -> SubprocessServer:
    """Server with a no-outcome fixture."""
    srv = SubprocessServer(
        data_dir=tmp_path / "uaa_harness_no_outcome",
        fixture_dir=FIXTURE_DIR,
        fixture_page="harness_no_outcome.html",
        enable_real_submission=True,
    )
    srv.start()
    yield srv
    srv.stop()


# ---------------------------------------------------------------------------
# 1. Valid approved submission
# ---------------------------------------------------------------------------


class TestValidApprovedSubmission:
    def test_valid_approved_submission(self, server: SubprocessServer) -> None:
        with server.client() as client:
            # Observe and approve.
            approval_id = _observe_and_approve(client, server.application_id)

            # Submit.
            resp = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is True
            assert body["state"] == "submitted_confirmed"

            # Verify fixture DOM click count.
            metrics = server.get_metrics()
            assert "click_count" in metrics, f"Metrics missing click_count: {metrics}"
            assert metrics["click_count"] == 1, f"Expected 1 click, got {metrics['click_count']}"


# ---------------------------------------------------------------------------
# 2. Feature disabled
# ---------------------------------------------------------------------------


class TestFeatureDisabled:
    def test_feature_disabled_zero_clicks(self, server_no_submit: SubprocessServer) -> None:
        with server_no_submit.client() as client:
            approval_id = _observe_and_approve(client, server_no_submit.application_id)

            resp = client.post(
                f"/api/submit/{server_no_submit.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is False
            assert body["state"] == "submission_not_allowed"

            metrics = server_no_submit.get_metrics()
            assert metrics["click_count"] == 0


# ---------------------------------------------------------------------------
# 3. Missing approval
# ---------------------------------------------------------------------------


class TestMissingApproval:
    def test_missing_approval_zero_clicks(self, server: SubprocessServer) -> None:
        with server.client() as client:
            resp = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": "nonexistent-id", "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is False
            assert body["state"] == "submission_not_allowed"

            metrics = server.get_metrics()
            assert metrics["click_count"] == 0


# ---------------------------------------------------------------------------
# 4. Stale snapshot
# ---------------------------------------------------------------------------


class TestStaleSnapshot:
    def test_stale_snapshot_zero_clicks(self, server: SubprocessServer) -> None:
        with server.client() as client:
            # Observe to create a snapshot.
            obs = client.post(f"/api/submit/{server.application_id}/observe")
            assert obs.status_code == 200

            # Get the approval_id from status.
            status = client.get(f"/api/submit/{server.application_id}/status").json()
            approval_id = status.get("snapshot", {}).get("active_approval_id")
            assert approval_id is not None

            # Create a new approval with a wrong URL (simulates stale).
            from universal_auto_applier.persistence.db import (
                build_engine_url,
                make_engine,
                make_session_factory,
                session_scope,
            )
            from universal_auto_applier.submission.models import (
                SubmissionSnapshot,
                SubmissionSnapshotField,
                SubmissionSnapshotSubmitControl,
            )
            from universal_auto_applier.submission.store import create_approval

            wrong_snap = SubmissionSnapshot(
                application_id=server.application_id,
                application_url="https://wrong-url.com",
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-1",
                        label="First name",
                        field_type="text",
                        filled_value="Mohamed",
                        status="filled",
                    )
                ],
                pending_intervention_count=0,
                submit_control=SubmissionSnapshotSubmitControl(
                    text="Submit application",
                    selector="button[type='submit']",
                ),
            ).with_hashes()

            # Insert the wrong snapshot directly into the DB.
            engine = make_engine(build_engine_url(server.get_db_path()))
            sf = make_session_factory(engine)
            with session_scope(sf) as session:
                create_approval(
                    session,
                    application_id=server.application_id,
                    snapshot=wrong_snap,
                )
                from universal_auto_applier.submission.store import get_active_approval

                approval = get_active_approval(session, server.application_id)
            engine.dispose()
            new_approval_id = approval.approval_id

            # Submit — should detect stale snapshot.
            resp = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": new_approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is False
            assert body["state"] == "approval_stale"

            metrics = server.get_metrics()
            assert metrics["click_count"] == 0


# ---------------------------------------------------------------------------
# 5. Pending intervention
# ---------------------------------------------------------------------------


class TestPendingIntervention:
    def test_pending_intervention_zero_clicks(self, server: SubprocessServer) -> None:
        with server.client() as client:
            # Create a pending intervention via the API.
            client.post(
                "/api/interventions",
                json={},
            )  # This won't work — need to use the resolve endpoint.
            # Actually, let's create an intervention directly via the
            # intervention resolve endpoint. But we need an intervention first.
            # Let's observe first, then create a fake intervention by
            # inserting directly into the DB.
            import sqlite3

            conn = sqlite3.connect(str(server.get_db_path()))
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO interventions (intervention_id, application_id, status, kind, "
                "question, options, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (
                    "test-iv-1",
                    server.application_id,
                    "pending",
                    "field_answer",
                    "Test question?",
                    "[]",
                ),
            )
            conn.commit()
            conn.close()

            # Observe and approve.
            approval_id = _observe_and_approve(client, server.application_id)

            # Submit — should be blocked by pending intervention.
            resp = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is False
            assert body["state"] == "submission_not_allowed"
            assert "pending" in body.get("error_message", "").lower()

            metrics = server.get_metrics()
            assert metrics["click_count"] == 0


# ---------------------------------------------------------------------------
# 6. Unknown outcome
# ---------------------------------------------------------------------------


class TestUnknownOutcome:
    def test_unknown_outcome_blocks_retry(self, server_no_outcome: SubprocessServer) -> None:
        with server_no_outcome.client() as client:
            approval_id = _observe_and_approve(client, server_no_outcome.application_id)

            # Submit — click happens but no confirmation detected.
            resp = client.post(
                f"/api/submit/{server_no_outcome.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["clicked"] is True
            assert body["state"] == "outcome_unknown"

            # Verify fixture click count = 1.
            metrics = server_no_outcome.get_metrics()
            assert metrics["click_count"] == 1

            # Retry — should be blocked (approval consumed).
            resp2 = client.post(
                f"/api/submit/{server_no_outcome.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["clicked"] is False

            # No additional click.
            metrics2 = server_no_outcome.get_metrics()
            assert metrics2["click_count"] == 1


# ---------------------------------------------------------------------------
# 7. Duplicate retry
# ---------------------------------------------------------------------------


class TestDuplicateRetry:
    def test_duplicate_retry_blocked(self, server: SubprocessServer) -> None:
        with server.client() as client:
            approval_id = _observe_and_approve(client, server.application_id)

            # First submit — should succeed.
            resp1 = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp1.status_code == 200
            body1 = resp1.json()
            assert body1["clicked"] is True

            # Second submit — should be blocked.
            resp2 = client.post(
                f"/api/submit/{server.application_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["clicked"] is False

            # Exactly one click.
            metrics = server.get_metrics()
            assert metrics["click_count"] == 1

            # Exactly one claim and one result.
            counts = _query_db_counts(server.get_db_path())
            assert counts["claims"] == 1
            assert counts["results"] == 1


# ---------------------------------------------------------------------------
# 8. Truly concurrent requests
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    def test_concurrent_one_click(self, server: SubprocessServer) -> None:
        with server.client() as client:
            approval_id = _observe_and_approve(client, server.application_id)

            results: list[dict[str, Any]] = []
            lock = threading.Lock()

            def make_request() -> None:
                try:
                    resp = client.post(
                        f"/api/submit/{server.application_id}/submit",
                        json={"approval_id": approval_id, "confirm": True},
                    )
                    with lock:
                        results.append(resp.json())
                except Exception as exc:
                    with lock:
                        results.append({"error": str(exc)})

            # Start both threads simultaneously.
            t1 = threading.Thread(target=make_request)
            t2 = threading.Thread(target=make_request)
            t1.start()
            t2.start()
            t1.join(timeout=120)
            t2.join(timeout=120)

            assert len(results) == 2

            # Exactly one click.
            clicked_count = sum(1 for r in results if r.get("clicked") is True)
            assert clicked_count == 1, (
                f"Expected exactly 1 click, got {clicked_count}. Results: {results}"
            )

            # The losing request must return a controlled response.
            for r in results:
                assert "error" not in r, f"Unhandled exception: {r}"

            # Verify fixture DOM click count = 1.
            metrics = server.get_metrics()
            assert metrics["click_count"] == 1

            # Exactly one claim and one result.
            counts = _query_db_counts(server.get_db_path())
            assert counts["claims"] == 1, f"Expected 1 claim, got {counts['claims']}"
            assert counts["results"] == 1, f"Expected 1 result, got {counts['results']}"
