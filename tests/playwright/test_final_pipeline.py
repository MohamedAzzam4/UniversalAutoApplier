"""Authoritative end-to-end pipeline regression test.

Exercises the complete UAA workflow through public API boundaries:
queue import, pipeline, interventions, retry, observe, high-risk
confirmation, approval, submission, and duplicate protection.

Workflow
--------
 1. Queue import via import_queue_file
 2. Pipeline first pass -> LinkedIn URL intervention -> NEEDS_USER_INPUT
 3. Resolve intervention via public API
 4. Retry -> QUEUED
 5. Pipeline second pass -> no interventions -> REVIEW_READY
 6. Observe live form with LLM -> high-risk salary field
 7. Verify document hashes
 8. Confirm high-risk + approve
 9. Snapshot staleness (modify form via fixture server, re-observe)
10. Controlled submission
11. Duplicate protection
12. Final steady state
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [
    pytest.mark.playwright,
]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "live_browser"


# ---------------------------------------------------------------------------
# Subprocess server helper
# ---------------------------------------------------------------------------


class SubprocessServer:
    """Manages the final_pipeline_server.py subprocess."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._base_url: str = ""
        self._fixture_port: int = 0

    def start(self) -> dict:
        data_dir = REPO_ROOT / "tmp_final_pipeline"
        data_dir.mkdir(parents=True, exist_ok=True)
        for f in data_dir.glob("*.sqlite*"):
            f.unlink()

        server_script = REPO_ROOT / "tests" / "harness" / "final_pipeline_server.py"
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["UAA_DEV"] = "1"
        self._proc = subprocess.Popen(
            [
                sys.executable,
                str(server_script),
                "--data-dir",
                str(data_dir),
                "--fixture-dir",
                str(FIXTURE_DIR),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        ready_data = self._wait_for_ready()
        self._base_url = f"http://127.0.0.1:{ready_data['port']}"
        self._fixture_port = ready_data["fixture_port"]

        deadline = time.time() + 10.0
        while time.time() < deadline:
            try:
                resp = httpx.get(f"{self._base_url}/api", timeout=2.0)
                if resp.status_code == 200:
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                time.sleep(0.5)

        return ready_data

    def _wait_for_ready(self) -> dict:
        buf = ""
        deadline = time.time() + 30.0
        while time.time() < deadline:
            assert self._proc is not None and self._proc.stdout is not None
            line = self._proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            buf += line
            if "READY:" in buf:
                idx = buf.index("READY:")
                payload = buf[idx + len("READY:") :].strip()
                return json.loads(payload)
        raise RuntimeError(f"Server did not become ready.\nCaptured stdout:\n{buf}")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def fixture_base_url(self) -> str:
        return f"http://127.0.0.1:{self._fixture_port}"

    def get_metrics(self) -> dict:
        resp = httpx.get(f"{self._base_url}/api/harness/metrics", timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def set_fixture_html(self, html: str) -> None:
        resp = httpx.post(
            f"{self.fixture_base_url}/set-html",
            json={"html": html},
            timeout=5.0,
        )
        resp.raise_for_status()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
            for pipe in (self._proc.stdout, self._proc.stderr):
                if pipe is not None:
                    pipe.close()
            self._proc = None


def _read_fixture_html(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The authoritative pipeline regression test
# ---------------------------------------------------------------------------


class TestFinalCompletePipeline:
    """One authoritative end-to-end pipeline regression test."""

    def test_full_pipeline_workflow(self) -> None:
        server = SubprocessServer()
        ready = server.start()
        base = server.base_url
        app_id = ready["application_id"]

        client: httpx.Client | None = None
        try:
            client = httpx.Client(base_url=base, timeout=120.0)

            # ================================================================
            # 1. Queue import verification
            #    The server imported one job via import_queue_file. Verify it.
            # ================================================================
            resp = client.get("/api/harness/application-id")
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"

            resp = client.get(f"/api/queue/{app_id}")
            assert resp.status_code == 200
            job_data = resp.json()
            assert job_data["application_id"] == app_id
            assert job_data["status"] == "queued"
            assert job_data["cv_pdf"] is not None
            assert job_data["cover_letter_pdf"] is not None

            # ================================================================
            # 2. Pipeline first pass -> LinkedIn URL intervention
            # ================================================================
            fixture_html = _read_fixture_html("final_pipeline_apply.html")

            resp = client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )
            assert resp.status_code == 200
            pipe1 = resp.json()
            assert pipe1["status"] == "completed"
            assert pipe1["jobs_processed"] == 1
            assert pipe1["jobs_succeeded"] == 1

            # Intervention created
            resp = client.get(
                "/api/interventions",
                params={"application_id": app_id, "pending_only": True},
            )
            assert resp.status_code == 200
            int_list = resp.json()
            assert int_list["total"] > 0

            linkedin_int = None
            for inv in int_list["interventions"]:
                q = (inv["question"] + " " + (inv["field_selector"] or "")).lower()
                if "linkedin" in q or "linkedin_url" in q:
                    linkedin_int = inv
                    break
            assert linkedin_int is not None, "LinkedIn URL intervention not found"
            linkedin_int_id = linkedin_int["intervention_id"]

            # Job is NEEDS_USER_INPUT
            resp = client.get(f"/api/queue/{app_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "needs_user_input"

            # Submission is blocked (no snapshot yet)
            resp = client.get(f"/api/submit/{app_id}/status")
            assert resp.status_code == 200
            status0 = resp.json()["snapshot"]
            assert not status0["can_submit"]

            # Fixture click count is zero
            metrics = server.get_metrics()
            assert metrics["click_count"] == 0

            # ================================================================
            # 3. Resolve intervention through public API
            #    The production change in resolve_intervention_endpoint also
            #    updates job.metadata.form_answers when save_to_memory=True,
            #    making the answer available to the deterministic mapper on
            #    the next pipeline pass.
            # ================================================================
            linkedin_answer = "https://linkedin.com/in/testuser"
            resp = client.post(
                f"/api/interventions/{linkedin_int_id}/resolve",
                json={
                    "resolution": "approved",
                    "answer": linkedin_answer,
                    "save_to_memory": True,
                },
            )
            assert resp.status_code == 200

            # ================================================================
            # 4. Retry -> QUEUED
            # ================================================================
            resp = client.post(f"/api/queue/{app_id}/retry")
            assert resp.status_code == 200
            resp = client.get(f"/api/queue/{app_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"

            # ================================================================
            # 5. Pipeline second pass -> no interventions -> REVIEW_READY
            # ================================================================
            resp = client.post(
                "/api/pipeline/start",
                json={"fixture_html": fixture_html, "max_jobs": 10},
            )
            assert resp.status_code == 200
            pipe2 = resp.json()
            assert pipe2["status"] == "completed"
            assert pipe2["jobs_succeeded"] == 1

            # No pending interventions
            resp = client.get(
                "/api/interventions",
                params={"application_id": app_id, "pending_only": True},
            )
            assert resp.status_code == 200
            assert resp.json()["total"] == 0

            # Job is REVIEW_READY
            resp = client.get(f"/api/queue/{app_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "review_ready"

            # ================================================================
            # 6. Observe live form with LLM -> high-risk salary field
            # ================================================================
            resp = client.post(f"/api/submit/{app_id}/observe")
            assert resp.status_code == 200
            observe = resp.json()
            snapshot = observe["snapshot"]
            snapshot_hash = snapshot["snapshot_hash"]
            assert snapshot_hash

            assert len(snapshot["fields"]) > 0
            field_tokens = [f["field_token"] for f in snapshot["fields"]]
            assert len(set(field_tokens)) == len(field_tokens)

            # LinkedIn URL field has the resolved value
            linkedin_field = None
            for f in snapshot["fields"]:
                if "linkedin" in f["label"].lower():
                    linkedin_field = f
                    break
            assert linkedin_field is not None, "LinkedIn field not in snapshot"
            assert linkedin_field["filled_value"] == linkedin_answer, (
                f"Expected {linkedin_answer}, got {linkedin_field['filled_value']!r}"
            )

            # Salary field is high-risk (LLM classified)
            salary_field = None
            for f in snapshot["fields"]:
                if "salary" in f["label"].lower():
                    salary_field = f
                    break
            assert salary_field is not None, "Salary field not in snapshot"
            assert salary_field["risk_level"] == "high", (
                f"Expected high risk level, got {salary_field['risk_level']!r}"
            )
            assert salary_field["requires_confirmation"]
            salary_token = salary_field["field_token"]

            # High-risk unconfirmed count > 0
            assert snapshot["unconfirmed_high_risk_count"] > 0

            # Snapshot is complete
            assert snapshot["is_complete"]
            assert snapshot["pending_intervention_count"] == 0

            # Submit control detected
            assert snapshot["submit_control"] is not None

            # ================================================================
            # -- Pre-confirmation high-risk gate proof --
            # ================================================================
            # Before high-risk confirmation, the snapshot must report
            # can_approve=False and can_submit=False.
            assert not snapshot["can_approve"], (
                f"can_approve must be False with unconfirmed high-risk, "
                f"got True (unconfirmed_high_risk_count={snapshot['unconfirmed_high_risk_count']})"
            )
            assert not snapshot["can_submit"], (
                f"can_submit must be False with unconfirmed high-risk, "
                f"got True (unconfirmed_high_risk_count={snapshot['unconfirmed_high_risk_count']})"
            )

            # Calling /approve before confirmation must be rejected with
            # an explicit "unconfirmed high-risk" error.
            resp = client.post(
                f"/api/submit/{app_id}/approve",
                json={"snapshot_hash": snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 409, (
                f"Expected 409 rejecting pre-confirmation approve, "
                f"got {resp.status_code}: {resp.json()}"
            )
            assert "unconfirmed high-risk" in resp.json()["detail"].lower(), (
                f"detail must mention unconfirmed high-risk: {resp.json()['detail']}"
            )

            # The approval exists (created by observe) but remains
            # unapproved — can_approve is False due to unconfirmed
            # high-risk fields.
            resp = client.get(f"/api/submit/{app_id}/status")
            status_before = resp.json()["snapshot"]
            assert status_before.get("approval_state") == "active", (
                f"Expected active approval_state, got {status_before.get('approval_state')}"
            )
            # No additional approval was created — the pre-confirmation
            # approve call (above) failed with 409 and did not change
            # the approval state.
            assert status_before.get("approved_snapshot_hash") == snapshot_hash, (
                f"approved_snapshot_hash should match the observed hash, "
                f"got {status_before.get('approved_snapshot_hash')!r}"
            )
            assert not status_before["can_approve"], "can_approve must remain False"
            assert not status_before["can_submit"], "can_submit must remain False"

            # Fixture click count is still zero.
            metrics = server.get_metrics()
            assert metrics["click_count"] == 0, (
                f"click_count must stay 0 before confirm, got {metrics['click_count']}"
            )

            # ================================================================
            # 7. Document filename and hash verification AND browser file upload proof
            # ================================================================
            metrics = server.get_metrics()
            docs = snapshot["documents"]
            assert len(docs) > 0

            cv_doc = None
            cover_doc = None
            for doc in docs:
                if doc["document_kind"] == "cv":
                    cv_doc = doc
                elif doc["document_kind"] == "cover_letter":
                    cover_doc = doc

            assert cv_doc is not None, "CV document not found in snapshot"
            assert cv_doc["filename"] == "cv.pdf", f"Expected cv.pdf, got {cv_doc['filename']!r}"
            assert cv_doc["content_hash"] == metrics["cv_hash"], (
                f"CV hash mismatch: {cv_doc['content_hash']} vs {metrics['cv_hash']}"
            )
            assert cv_doc["exists"]
            assert cv_doc["readable"]

            assert cover_doc is not None, "Cover letter not found in snapshot"
            assert cover_doc["filename"] == "cover.pdf", (
                f"Expected cover.pdf, got {cover_doc['filename']!r}"
            )
            assert cover_doc["content_hash"] == metrics["cover_hash"], (
                f"Cover hash mismatch: {cover_doc['content_hash']} vs {metrics['cover_hash']}"
            )
            assert cover_doc["exists"]
            assert cover_doc["readable"]

            # Browser file input proof: the real browser file inputs received
            # the correct filenames via Playwright's setInputFiles.  The
            # change-event handlers in the fixture HTML reported them back
            # to the fixture server.  Poll briefly if the events haven't
            # arrived yet (async POST from the browser).
            deadline = time.time() + 3.0
            file_metrics = metrics
            while time.time() < deadline:
                file_metrics = server.get_metrics()
                if (
                    file_metrics.get("cv_filename") == "cv.pdf"
                    and file_metrics.get("cover_filename") == "cover.pdf"
                ):
                    break
                time.sleep(0.2)
            assert file_metrics["cv_filename"] == "cv.pdf", (
                f"Browser CV input filename: expected cv.pdf, got {file_metrics['cv_filename']!r}"
            )
            assert file_metrics["cover_filename"] == "cover.pdf", (
                f"Browser cover-letter input filename: expected cover.pdf, "
                f"got {file_metrics['cover_filename']!r}"
            )

            # ================================================================
            # 8. Confirm high-risk fields + approve
            # ================================================================
            # Collect ALL high-risk field tokens from the snapshot.
            high_risk_tokens = [
                f["field_token"]
                for f in snapshot["fields"]
                if f["risk_level"] == "high" or f["requires_confirmation"]
            ]
            assert salary_token in high_risk_tokens

            resp = client.post(
                f"/api/submit/{app_id}/confirm-high-risk",
                json={
                    "snapshot_hash": snapshot_hash,
                    "field_tokens": high_risk_tokens,
                    "confirm": True,
                },
            )
            assert resp.status_code == 200
            confirmed = resp.json()
            assert salary_token in confirmed["confirmed_tokens"]

            # Check status to see if unconfirmed_high_risk_count is now 0
            status_resp = client.get(f"/api/submit/{app_id}/status")
            status_data = status_resp.json()
            status_snapshot = status_data.get("snapshot", {})
            assert status_snapshot.get("unconfirmed_high_risk_count", -1) == 0, (
                f"Expected 0 unconfirmed after confirm, got {status_snapshot.get('unconfirmed_high_risk_count')}"
            )
            assert status_snapshot.get("can_approve")

            # Approve
            resp = client.post(
                f"/api/submit/{app_id}/approve",
                json={"snapshot_hash": snapshot_hash, "confirm": True},
            )
            assert resp.status_code == 200, f"Approve failed: {resp.json()}"
            approval_id = resp.json()["approval_id"]
            assert approval_id

            # can_submit is True
            resp = client.get(f"/api/submit/{app_id}/status")
            assert resp.status_code == 200
            assert resp.json()["snapshot"]["can_submit"]

            # ================================================================
            # 9. Snapshot staleness
            # ================================================================
            # Modify fixture HTML via the fixture server's /set-html endpoint
            # (simulating the ATS changing their application form).
            modified_html = fixture_html.replace(
                ">Desired Salary<",
                ">Desired Annual Salary<",
            )
            server.set_fixture_html(modified_html)

            # Re-observe -> new snapshot hash, old approval revoked
            resp = client.post(f"/api/submit/{app_id}/observe")
            assert resp.status_code == 200
            new_snapshot = resp.json()["snapshot"]
            new_hash = new_snapshot["snapshot_hash"]
            assert new_hash != snapshot_hash

            # Old approval is stale: submit with old approval_id -> blocked
            resp = client.post(
                f"/api/submit/{app_id}/submit",
                json={"approval_id": approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            stale_submit = resp.json()
            assert not stale_submit["clicked"]
            assert stale_submit["state"] == "submission_not_allowed"

            # New snapshot needs high-risk confirmation again
            assert new_snapshot["unconfirmed_high_risk_count"] > 0
            assert not new_snapshot["can_submit"]

            # Confirm high-risk on new snapshot
            new_high_risk_tokens = [
                f["field_token"]
                for f in new_snapshot["fields"]
                if f["risk_level"] == "high" or f["requires_confirmation"]
            ]
            assert len(new_high_risk_tokens) > 0
            resp = client.post(
                f"/api/submit/{app_id}/confirm-high-risk",
                json={
                    "snapshot_hash": new_hash,
                    "field_tokens": new_high_risk_tokens,
                    "confirm": True,
                },
            )
            assert resp.status_code == 200

            # Approve new snapshot
            resp = client.post(
                f"/api/submit/{app_id}/approve",
                json={"snapshot_hash": new_hash, "confirm": True},
            )
            assert resp.status_code == 200
            new_approval_id = resp.json()["approval_id"]
            assert new_approval_id
            assert new_approval_id != approval_id

            # can_submit restored
            resp = client.get(f"/api/submit/{app_id}/status")
            assert resp.status_code == 200
            assert resp.json()["snapshot"]["can_submit"]

            # ================================================================
            # 10. Controlled submission
            # ================================================================
            resp = client.post(
                f"/api/submit/{app_id}/submit",
                json={"approval_id": new_approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            submit = resp.json()
            assert submit["clicked"], f"Submit must click the button, state: {submit['state']}"
            assert submit["state"] == "submitted_confirmed", (
                f"Expected submitted_confirmed, got {submit['state']}: "
                f"{submit.get('error_message', '')}"
            )
            assert "thank" in submit.get("confirmation_evidence", "").lower(), (
                f"Confirmation evidence must contain 'thank', got: "
                f"{submit.get('confirmation_evidence', '')}"
            )

            # Fixture received exactly one click
            metrics = server.get_metrics()
            assert metrics["click_count"] == 1

            # Submit-time file upload proof: the fixture's submit handler
            # inspected the real file inputs and reported their filenames.
            assert metrics.get("uploaded_cv_at_submit") == "cv.pdf", (
                f"Submit-time CV filename: expected cv.pdf, "
                f"got {metrics.get('uploaded_cv_at_submit')!r}"
            )
            assert metrics.get("uploaded_cover_at_submit") == "cover.pdf", (
                f"Submit-time cover-letter filename: expected cover.pdf, "
                f"got {metrics.get('uploaded_cover_at_submit')!r}"
            )
            # Document hashes in snapshot still match the actual files.
            assert cv_doc["content_hash"] == metrics["cv_hash"], (
                f"CV hash mismatch after submit: {cv_doc['content_hash']} vs {metrics['cv_hash']}"
            )
            assert cover_doc["content_hash"] == metrics["cover_hash"], (
                f"Cover hash mismatch after submit: {cover_doc['content_hash']} vs {metrics['cover_hash']}"
            )

            # Persisted result references the exact approval ID and snapshot hash
            # Check via the harness endpoint (direct DB query) first.
            # The stale-attempt result from step 9 also recorded a result,
            # so total results = 2. The latest should be the successful one.
            resp = client.get(
                "/api/harness/submission-results",
                params={"application_id": app_id},
            )
            assert resp.status_code == 200
            results_data = resp.json()
            assert results_data["total"] == 2, (
                f"Expected 2 submission results (stale attempt + success), got {results_data['total']}"
            )
            # The latest result (last in ordered list) is the successful one.
            latest_r = results_data["results"][-1]
            assert latest_r["approval_id"] == new_approval_id, (
                f"result approval_id {latest_r['approval_id']} vs expected {new_approval_id}"
            )
            assert latest_r["snapshot_hash_at_submit"] == new_hash, (
                f"result snapshot_hash {latest_r['snapshot_hash_at_submit']} vs expected {new_hash}"
            )
            assert latest_r["state"] == "submitted_confirmed"
            assert latest_r["clicked"] is True

            # Verify the status endpoint also returns the result references
            resp = client.get(f"/api/submit/{app_id}/status")
            assert resp.status_code == 200
            final_status = resp.json()["snapshot"]
            assert final_status["latest_submission_state"] == "submitted_confirmed"
            assert final_status["latest_submission_approval_id"] == new_approval_id, (
                f"status endpoint approval_id mismatch: "
                f"{final_status['latest_submission_approval_id']} vs {new_approval_id}"
            )
            assert final_status["latest_submission_snapshot_hash"] == new_hash, (
                f"status endpoint snapshot_hash mismatch: "
                f"{final_status['latest_submission_snapshot_hash']} vs {new_hash}"
            )

            # ================================================================
            # 11. Duplicate protection
            # ================================================================
            resp = client.post(
                f"/api/submit/{app_id}/submit",
                json={"approval_id": new_approval_id, "confirm": True},
            )
            assert resp.status_code == 200
            dup = resp.json()
            assert not dup["clicked"], f"Duplicate must be blocked, state: {dup['state']}"
            assert dup["state"] == "submission_not_allowed", (
                f"Expected blocked state, got {dup['state']}"
            )

            # Fixture still has exactly one click
            metrics = server.get_metrics()
            assert metrics["click_count"] == 1

            # Still only 2 SubmissionResults (stale attempt + success; duplicate did not add one)
            resp = client.get(
                "/api/harness/submission-results",
                params={"application_id": app_id},
            )
            assert resp.status_code == 200
            results_after_dup = resp.json()
            assert results_after_dup["total"] == 2, (
                f"Expected 2 results after duplicate block, got {results_after_dup['total']}"
            )
            # All clicked states: first is submission_not_allowed, second is submitted_confirmed
            clicked_states = [(r["clicked"], r["state"]) for r in results_after_dup["results"]]
            assert clicked_states == [
                (False, "submission_not_allowed"),
                (True, "submitted_confirmed"),
            ], f"Unexpected result states: {clicked_states}"

            # ================================================================
            # 12. Final steady state
            #     After browser submission through the submit API, the job
            #     status is review_ready (the pipeline-orchestrator path
            #     would set submitted, but the browser path does not change
            #     the job status — the SubmissionResult records the outcome).
            # ================================================================
            resp = client.get(f"/api/queue/{app_id}")
            assert resp.status_code == 200
            final_job = resp.json()
            assert final_job["status"] == "review_ready", (
                f"Expected review_ready final state, got {final_job['status']!r}"
            )

        finally:
            if client is not None:
                client.close()
            server.stop()
