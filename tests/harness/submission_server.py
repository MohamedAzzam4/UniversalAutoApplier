"""Subprocess test server for cross-version controlled-submission tests.

This module is run as a subprocess by the test harness. It owns the
entire UAA app + Playwright lifecycle. The parent pytest process sends
HTTP requests to it.
"""

from __future__ import annotations

import argparse
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


def _start_fixture_server(
    fixture_dir: Path,
) -> tuple[int, ThreadingHTTPServer, threading.Thread, dict]:
    """Start a fixture HTTP server that also tracks submit clicks.

    Returns (port, server, thread, metrics_dict). The metrics_dict is
    updated in real-time as clicks happen.
    """
    metrics = {"click_count": 0, "confirmation_count": 0}
    fixture_path = fixture_dir / "harness_submit.html"
    no_outcome_path = fixture_dir / "harness_no_outcome.html"
    html_content = fixture_path.read_text(encoding="utf-8")
    no_outcome_content = no_outcome_path.read_text(encoding="utf-8")

    class _MetricsHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            del args

        def do_GET(self) -> None:
            if self.path == "/metrics":
                import json as _json

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json.dumps(metrics).encode())
            elif self.path == "/harness_submit.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html_content.encode())
            elif self.path == "/harness_no_outcome.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(no_outcome_content.encode())
            else:
                # Serve other files from the fixture directory.
                super().do_GET()

        def do_POST(self) -> None:
            if self.path == "/click":
                metrics["click_count"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                import json as _json

                self.wfile.write(
                    _json.dumps({"ok": True, "count": metrics["click_count"]}).encode()
                )
            elif self.path == "/confirm":
                metrics["confirmation_count"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

    port = _find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port, server, thread, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Submission test server")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--fixture-page", default="harness_submit.html", type=str)
    parser.add_argument("--enable-real-submission", default="true", type=str)
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)

    fixture_port, fixture_server, fixture_thread, fixture_metrics = _start_fixture_server(
        args.fixture_dir
    )
    fixture_base_url = f"http://127.0.0.1:{fixture_port}"

    from universal_auto_applier.config import Settings

    api_port = _find_free_port()
    settings = Settings(
        host="127.0.0.1",
        port=api_port,
        data_dir=args.data_dir,
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=args.enable_real_submission.lower() == "true",
    )

    from universal_auto_applier.core.identity import compute_application_id
    from universal_auto_applier.core.models import ApplicationJob
    from universal_auto_applier.core.statuses import ApplicationStatus, Platform
    from universal_auto_applier.persistence.db import (
        build_engine_url,
        make_engine,
        make_session_factory,
        session_scope,
    )
    from universal_auto_applier.persistence.job_repository import upsert_application_job
    from universal_auto_applier.persistence.migrations import apply_migrations
    from universal_auto_applier.persistence.models import Base

    db_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    apply_migrations(db_url)
    engine = make_engine(db_url)
    sf = make_session_factory(engine)

    job_url = f"{fixture_base_url}/{args.fixture_page}"
    cv_pdf = args.data_dir / "cv.pdf"
    cover_pdf = args.data_dir / "cover.pdf"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")

    job = ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="harness-1", url=job_url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test Corp",
        title="Software Engineer",
        url=job_url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="harness-1",
        metadata={
            "candidate_profile": {
                "first_name": "Mohamed",
                "last_name": "Azzam",
                "full_name": "Mohamed Azzam",
                "email": "mohamed@example.com",
                "phone": "+49 1234567",
                "requires_sponsorship": False,
            },
        },
    )

    with session_scope(sf) as session:
        upsert_application_job(session, job)

    from universal_auto_applier.api.app import create_app

    app = create_app(settings=settings)

    # Seed the database using a direct engine (not the app's lifespan engine).
    # The app's lifespan will create its own engine pointing to the same
    # SQLite file, so the seeded data will be visible.
    from universal_auto_applier.persistence.db import (
        build_engine_url as _beu,
        make_engine as _me,
    )

    engine2 = _me(_beu(settings.data_dir / "uaa.sqlite"))
    Base.metadata.create_all(engine2)

    # Add a harness metrics endpoint that fetches the fixture page and
    # returns the click count from the DOM.
    from fastapi import APIRouter as _AR

    harness_router = _AR(tags=["harness"])

    @harness_router.get("/harness/metrics")
    def get_harness_metrics() -> dict:
        """Return the fixture's click-count metrics from the in-memory counter."""
        return {
            "click_count": fixture_metrics["click_count"],
            "confirmation_count": fixture_metrics["confirmation_count"],
            "fixture_url": job_url,
        }

    app.include_router(harness_router, prefix="/api")

    from universal_auto_applier.submission.execution_service import FixtureContextFactory

    app.state.submission_context_factory = FixtureContextFactory(headless=True)

    readiness = json.dumps(
        {
            "port": api_port,
            "application_id": job.application_id,
            "fixture_url": job_url,
            "fixture_port": fixture_port,
        }
    )
    print(f"READY:{readiness}", flush=True)

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
    server = uvicorn.Server(config)

    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            fixture_server.shutdown()
            fixture_server.server_close()
        except Exception:
            pass
        try:
            engine.dispose()
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
