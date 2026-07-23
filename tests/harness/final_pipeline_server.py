"""Subprocess test server for the final pipeline regression test.

The server owns a fixture HTTP server and the UAA FastAPI application.
The test process drives everything through HTTP.

Architecture
------------
- Fixture server: serves local HTML, tracks click/document metrics
- UAA FastAPI: routes for queue, pipeline, interventions, submit
- Harness routes (read-only only): /api/harness/metrics, /api/harness/application-id

Public boundaries exercised
--------------------------
- Queue import (import_queue_file)
- Pipeline start (POST /api/pipeline/start)
- Interventions (GET /api/interventions, POST /api/interventions/{id}/resolve)
- Retry (POST /api/queue/{id}/retry)
- Observe / confirm-high-risk / approve / submit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MetricsHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves fixture HTML and tracks clicks/uploads."""

    metrics: dict = {
        "click_count": 0,
        "confirmation_count": 0,
        "cv_filename": "",
        "cover_filename": "",
        "uploaded_cv_at_submit": "",
        "uploaded_cover_at_submit": "",
    }
    upload_log: list[str] = []
    fixture_html: str = ""
    landing_html: str = ""

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _send_json(self, data: object, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _read_body(self) -> str:
        content_length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(content_length).decode() if content_length else ""

    def do_GET(self) -> None:
        if self.path == "/metrics":
            self._send_json(self.metrics)
        elif self.path == "/final_pipeline_landing.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(self.landing_html.encode())
        elif self.path == "/final_pipeline_apply.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(self.fixture_html.encode())
        else:
            super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/click":
            self.metrics["click_count"] += 1
            self._send_json({"ok": True})
        elif self.path == "/confirm":
            self.metrics["confirmation_count"] += 1
            self._send_json({"ok": True})
        elif self.path == "/upload":
            body = self._read_body()
            data = json.loads(body) if body else {}
            filename = data.get("filename", "unknown")
            self.upload_log.append(filename)
            self._send_json({"ok": True})
        elif self.path == "/record-file":
            body = self._read_body()
            data = json.loads(body) if body else {}
            input_name = data.get("input", "")
            filename = data.get("filename", "")
            if input_name == "cv_upload":
                self.metrics["cv_filename"] = filename
            elif input_name == "cover_letter":
                self.metrics["cover_filename"] = filename
            self._send_json({"ok": True})
        elif self.path == "/record-submit-files":
            body = self._read_body()
            data = json.loads(body) if body else {}
            self.metrics["uploaded_cv_at_submit"] = data.get("cv_filename", "")
            self.metrics["uploaded_cover_at_submit"] = data.get("cover_filename", "")
            self._send_json({"ok": True})
        elif self.path == "/set-html":
            body = self._read_body()
            data = json.loads(body) if body else {}
            html = data.get("html", "")
            if html:
                type(self).fixture_html = html
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "html required"}, 400)
        else:
            self.send_response(404)
            self.end_headers()


def _start_fixture_server(
    fixture_dir: Path,
) -> tuple[int, ThreadingHTTPServer, threading.Thread]:
    """Start a fixture HTTP server. Returns (port, server, thread)."""
    _MetricsHandler.fixture_html = (fixture_dir / "final_pipeline_apply.html").read_text(
        encoding="utf-8"
    )
    _MetricsHandler.landing_html = (fixture_dir / "final_pipeline_landing.html").read_text(
        encoding="utf-8"
    )
    _MetricsHandler.metrics = {
        "click_count": 0,
        "confirmation_count": 0,
        "cv_filename": "",
        "cover_filename": "",
        "uploaded_cv_at_submit": "",
        "uploaded_cover_at_submit": "",
    }
    _MetricsHandler.upload_log = []

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port, server, thread


# ---------------------------------------------------------------------------
# Conditional mock QA service
# ---------------------------------------------------------------------------


class _ConditionalMockQA:
    """Mocks the LLM QA service for deterministic test behavior.

    - For questions whose text contains "linkedin" (case-insensitive):
      returns refusal so the field becomes intervention_needed.
    - For everything else: answers "yes" with auto-fill.
    """

    def answer_question(
        self,
        question: object,
        category: object,
        ledger: object,
    ) -> object:
        from universal_auto_applier.llm.models import QuestionResolution
        from universal_auto_applier.llm.qa_service import MockQuestionAnsweringService
        from universal_auto_applier.llm.question_classifier import QuestionRisk

        q_text = (
            str(question.question_text) if hasattr(question, "question_text") else str(question)
        )
        if "linkedin" in q_text.lower():
            return QuestionResolution(
                question=question,
                category=category,
                risk_level=QuestionRisk.HIGH,
                proposed_answer=None,
                requires_human_confirmation=True,
                refusal="Field requires human input: LinkedIn URL must be provided by the user",
            )
        inner = MockQuestionAnsweringService(answer="yes", refused=False)
        return inner.answer_question(question, category, ledger)


# ---------------------------------------------------------------------------
# Answer memory loader helper
# ---------------------------------------------------------------------------


# ===================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Final pipeline test server")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)

    # Clean any stale SQLite DB from a previous run.
    for f in args.data_dir.glob("*.sqlite*"):
        f.unlink()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    # Start fixture server.
    fixture_port, fixture_server, fixture_thread = _start_fixture_server(args.fixture_dir)
    fixture_base_url = f"http://127.0.0.1:{fixture_port}"

    # Create synthetic document files with unique content per port.
    cv_path = args.data_dir / "cv.pdf"
    cover_path = args.data_dir / "cover.pdf"
    cv_path.write_bytes(b"%PDF-1.4 test cv " + str(fixture_port).encode())
    cover_path.write_bytes(b"%PDF-1.4 test cover " + str(fixture_port).encode())

    # Pre-compute SHA-256 hashes for verification.
    _cv_hash = hashlib.sha256(cv_path.read_bytes()).hexdigest()[:32]
    _cover_hash = hashlib.sha256(cover_path.read_bytes()).hexdigest()[:32]

    # ── Build the UAA app ──────────────────────────────────────────────
    from universal_auto_applier.config import Settings

    api_port = _find_free_port()
    settings = Settings(
        host="127.0.0.1",
        port=api_port,
        data_dir=args.data_dir,
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )

    job_url = f"{fixture_base_url}/final_pipeline_apply.html"

    from universal_auto_applier.core.identity import compute_application_id
    from universal_auto_applier.core.statuses import ApplicationStatus, Platform

    app_id = compute_application_id(
        platform=str(Platform.GENERIC),
        external_job_id="final-pipeline-1",
        url=job_url,
    )

    # Set up the database and import the job.
    from universal_auto_applier.persistence.db import (
        build_engine_url,
        make_engine,
        make_session_factory,
    )
    from universal_auto_applier.persistence.migrations import apply_migrations

    db_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    apply_migrations(db_url)
    import_engine = make_engine(db_url)
    import_sf = make_session_factory(import_engine)

    jsonl_path = args.data_dir / "queue.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "application_id": app_id,
                    "platform": str(Platform.GENERIC),
                    "source": "test",
                    "company": "Test Corp",
                    "title": "Software Engineer",
                    "url": job_url,
                    "verdict": "apply",
                    "cv_pdf": str(cv_path),
                    "cover_letter_pdf": str(cover_path),
                    "status": str(ApplicationStatus.QUEUED),
                    "external_job_id": "final-pipeline-1",
                    "metadata": {
                        "candidate_profile": {
                            "first_name": "Test",
                            "last_name": "Candidate",
                            "full_name": "Test Candidate",
                            "email": "test.candidate@example.com",
                            "phone": "+49 1111111111",
                            "requires_sponsorship": False,
                        },
                    },
                }
            )
        )
        f.write("\n")

    from universal_auto_applier.application_queue.importer import import_queue_file

    iresult = import_queue_file(jsonl_path, import_sf)
    assert iresult.imported == 1, f"Expected 1 imported job, got {iresult.imported}"
    assert iresult.errors == [], f"Queue import errors: {iresult.errors}"

    import_engine.dispose()

    # ── Create the FastAPI app ─────────────────────────────────────────
    from universal_auto_applier.api.app import create_app

    app = create_app(settings=settings)

    # The lifecycle will create the engine/session_factory. Pre-set them
    # so the lifespan reuses our factory.
    app.state.engine = make_engine(db_url)
    app.state.session_factory = make_session_factory(app.state.engine)

    # Register the FixtureContextFactory.
    from universal_auto_applier.submission.execution_service import FixtureContextFactory

    app.state.submission_context_factory = FixtureContextFactory(headless=True)

    # ── Patch execute_live_form in the execution_service module ──────────
    # The observe endpoint calls execute_live_form() which is the
    # deterministic-only path.  We replace that ONE reference so that
    # deterministic fill is followed by LLM resolution for ALL non-filled
    # fields (including ``skipped`` optional fields), not just those with
    # status intervention_needed/blocked/failed.
    #
    # Critically, we do NOT patch live_executor.execute_live_form itself —
    # the LLM resolver must be able to call the original for the initial
    # deterministic pass without recursion.
    import universal_auto_applier.form_engine.live_executor as _le_mod
    import universal_auto_applier.submission.execution_service as _es_mod

    _LLM_QA = _ConditionalMockQA()

    def _patched_execute(page, candidate, job):  # type: ignore[no-untyped-def]
        # 1. Deterministic fill (original, no recursion).
        execution = _le_mod.execute_live_form(page, candidate, job)

        # 2. Re-extract live fields for LLM processing.
        targets = _le_mod._extract_live_fields(page)
        target_by_token: dict[str, object] = {t.token: t for t in targets}

        # 3. Collect all non-filled fields (skipped + intervention_needed).
        unresolved_tokens: set[str] = set()
        for record in execution.fields:
            if record.status in {"skipped", "intervention_needed", "blocked", "failed"}:
                if record.field_token:
                    unresolved_tokens.add(record.field_token)

        if unresolved_tokens:
            from universal_auto_applier.llm.question_resolver import resolve_question

            # Candidate facts for the LLM's truth ledger.
            # Only candidate profile facts — answer memory is loaded by
            # production code (the resolve endpoint writes to form_answers,
            # which the deterministic mapper reads).
            candidate_facts = (
                list(candidate.to_answer_memory_facts())
                if candidate and hasattr(candidate, "to_answer_memory_facts")
                else []
            )

            for token in unresolved_tokens:
                target = target_by_token.get(token)
                if target is None:
                    continue

                resolution = resolve_question(
                    target.field,
                    candidate,
                    job,
                    qa_service=_LLM_QA,
                    answer_memory_facts=candidate_facts,
                )

                for i, record in enumerate(execution.fields):
                    if record.field_token != token:
                        continue

                    if resolution.can_auto_fill and resolution.proposed_answer is not None:
                        proposed = resolution.proposed_answer
                        fill_value = proposed.normalized_value or proposed.value
                        is_valid, _reason = _le_mod.validate_typed_answer(
                            target.field.type, fill_value, target.field.options
                        )
                        if is_valid:
                            try:
                                actual = _le_mod._execute_field(target, fill_value)
                                execution.fields[i] = _le_mod.LiveFieldRecord(
                                    page_url=record.page_url,
                                    selector=record.selector,
                                    label=record.label,
                                    field_type=record.field_type,
                                    status="filled",
                                    source="llm_grounded",
                                    field_token=token,
                                    proposed_answer=proposed.value,
                                    confidence=proposed.confidence,
                                    evidence_summary="; ".join(e.fact for e in proposed.evidence),
                                    category=str(resolution.category),
                                    risk_level=str(resolution.risk_level),
                                    requires_confirmation=False,
                                    options=[
                                        opt.label or opt.value for opt in target.field.options
                                    ],
                                    selected_value=actual,
                                    filled_value=actual,
                                )
                                execution.filled += 1
                            except Exception:
                                execution.fields[i] = _le_mod.LiveFieldRecord(
                                    page_url=record.page_url,
                                    selector=record.selector,
                                    label=record.label,
                                    field_type=record.field_type,
                                    status="failed",
                                    source="llm_grounded",
                                    field_token=token,
                                    proposed_answer=proposed.value,
                                    confidence=proposed.confidence,
                                    category=str(resolution.category),
                                    risk_level=str(resolution.risk_level),
                                    requires_confirmation=True,
                                )
                        else:
                            execution.fields[i] = _le_mod.LiveFieldRecord(
                                page_url=record.page_url,
                                selector=record.selector,
                                label=record.label,
                                field_type=record.field_type,
                                status="intervention_needed",
                                source="llm_grounded",
                                field_token=token,
                                proposed_answer=proposed.value,
                                confidence=proposed.confidence,
                                category=str(resolution.category),
                                risk_level=str(resolution.risk_level),
                                requires_confirmation=True,
                            )
                    else:
                        # High-risk / refused / unresolved.
                        # Keep optional skipped fields as ``skipped`` so
                        # they don't count toward unresolved_required_count
                        # (``skipped`` is not in _UNRESOLVED_STATUSES).
                        p = resolution.proposed_answer
                        new_status = (
                            "skipped" if record.status == "skipped" else "intervention_needed"
                        )
                        execution.fields[i] = _le_mod.LiveFieldRecord(
                            page_url=record.page_url,
                            selector=record.selector,
                            label=record.label,
                            field_type=record.field_type,
                            status=new_status,
                            source="llm_grounded" if p else None,
                            field_token=token,
                            proposed_answer=p.value if p else None,
                            confidence=p.confidence if p else None,
                            evidence_summary="; ".join(e.fact for e in p.evidence) if p else "",
                            category=str(resolution.category),
                            risk_level=str(resolution.risk_level),
                            requires_confirmation=True,
                        )
                    break

            execution.fields = _le_mod.consolidate_fields(execution.fields)
            execution.validation_errors = _le_mod._validation_errors(page)

        return execution

    _es_mod.execute_live_form = _patched_execute

    # ── Register harness API routes (read-only only) ─────────────────────
    from fastapi import APIRouter, HTTPException

    harness_router = APIRouter(tags=["harness"])

    @harness_router.get("/harness/metrics")
    def get_harness_metrics() -> dict:
        return {
            "click_count": _MetricsHandler.metrics["click_count"],
            "cv_hash": _cv_hash,
            "cover_hash": _cover_hash,
            "cv_path": str(cv_path),
            "cover_path": str(cover_path),
            "cv_filename": _MetricsHandler.metrics["cv_filename"],
            "cover_filename": _MetricsHandler.metrics["cover_filename"],
            "uploaded_cv_at_submit": _MetricsHandler.metrics["uploaded_cv_at_submit"],
            "uploaded_cover_at_submit": _MetricsHandler.metrics["uploaded_cover_at_submit"],
            "upload_log": _MetricsHandler.upload_log,
            "fixture_url": job_url,
        }

    @harness_router.get("/harness/submission-results")
    def get_submission_results_ep(application_id: str = "") -> dict:
        from universal_auto_applier.persistence.db import session_scope
        from universal_auto_applier.persistence.models import SubmissionResultRow

        with session_scope(app.state.session_factory) as session:
            q = session.query(SubmissionResultRow)
            if application_id:
                q = q.filter(SubmissionResultRow.application_id == application_id)
            results = q.order_by(SubmissionResultRow.attempted_at.asc()).all()
            return {
                "total": len(results),
                "results": [
                    {
                        "result_id": r.result_id,
                        "application_id": r.application_id,
                        "approval_id": r.approval_id,
                        "snapshot_hash_at_submit": r.snapshot_hash_at_submit,
                        "state": r.state,
                        "clicked": r.clicked,
                    }
                    for r in results
                ],
            }

    @harness_router.get("/harness/application-id")
    def get_app_id_ep() -> dict:
        from universal_auto_applier.persistence.db import session_scope
        from universal_auto_applier.persistence.job_repository import get_application_job

        with session_scope(app.state.session_factory) as session:
            job_row = get_application_job(session, app_id)
            if job_row is None:
                raise HTTPException(404, "job not found")
            return {
                "application_id": job_row.application_id,
                "status": str(job_row.status),
            }

    app.include_router(harness_router, prefix="/api")

    # ── Ready signal ────────────────────────────────────────────────────
    readiness = json.dumps(
        {
            "port": api_port,
            "application_id": app_id,
            "fixture_url": job_url,
            "fixture_port": fixture_port,
        }
    )
    print(f"READY:{readiness}", flush=True)

    # ── Run ─────────────────────────────────────────────────────────────
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=api_port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    uvicorn_server = uvicorn.Server(config)
    try:
        uvicorn_server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            fixture_server.shutdown()
            fixture_server.server_close()
        except Exception:
            pass
        try:
            import_engine.dispose()
        except Exception:
            pass
        ctx_factory = getattr(app.state, "submission_context_factory", None)
        if ctx_factory is not None:
            try:
                ctx_factory.close()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
