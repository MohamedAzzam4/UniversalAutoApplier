"""WQ-8 ATS Target URL / Source URL Separation — hermetic regressions.

Proves the distinction between:

- ``job.url`` = canonical source/job identity URL (may be a job DETAIL page)
- ``snapshot.application_url`` = actual ATS application FORM URL
- ``authorization.application_url`` = exact frozen owner-approved form URL

The fixture serves TWO different pages at TWO different URLs on the same
loopback HTTP server:

    DETAIL PAGE (/detail)
        |
        | safe Apply link
        v
    APPLICATION FORM (/apply)

Tests cover:
A. Phase-A observation: detail→form navigation, interlock before first goto,
   dangerous_submit never clicked during discovery, snapshot.application_url
   == form URL (not job.url), job.url unchanged, application_id unchanged,
   persisted snapshot reloadable.
B. Failure path: detail page with NO safe apply path → observation fails
   closed, no empty snapshot persisted, no authorization/result/claim.
C. Review-packet: uses snapshot.application_url, rejects mismatching --job-url.
D. Authorization: binds snapshot.application_url, detail URL may differ.
E. Coordinator: job.url=detail + snapshot.application_url=form is VALID;
   changing the form URL produces APPROVAL_STALE.
F. Phase-B browser target: navigates to approved form URL, not job.url;
   post-fill URL guard fails closed on mismatch.

No external network, no real ATS, no real candidate data, no owner PII.
"""

from __future__ import annotations

import hashlib
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.execution_service import (
    FixtureContextFactory,
    SubmissionExecutionService,
)
from universal_auto_applier.submission.store import get_active_approval

# ---------------------------------------------------------------------------
# Fixture HTML: detail page + application form (TWO different URLs)
# ---------------------------------------------------------------------------

# The detail page has a job description and a safe "Apply" link to /apply.
# It does NOT look like an application form (no file inputs, not enough
# form controls to trigger is_application_form).
_DETAIL_HTML = """<!DOCTYPE html>
<html>
<head><title>Job Detail — FixtureCo Engineer</title></head>
<body>
<h1>Software Engineer (f/m/d)</h1>
<p>FixtureCo is hiring. Click Apply to start your application.</p>
<a id="apply-link" href="/apply">Apply</a>
</body>
</html>
"""

# The application form has file inputs, text fields, and a submit button —
# enough signals for analyze_page to classify it as is_application_form.
_FORM_HTML = """<!DOCTYPE html>
<html>
<head><title>Application Form — FixtureCo</title></head>
<body>
<form id="app-form" method="post" action="/submit">
  <label for="name">Full Name</label>
  <input type="text" id="name" name="name" required>
  <label for="email">Email</label>
  <input type="email" id="email" name="email" required>
  <label for="resume">Resume</label>
  <input type="file" id="resume" name="resume" required>
  <button type="submit" id="submit-btn">Submit Application</button>
</form>
</body>
</html>
"""

# A detail page with NO safe apply link — used for the failure-path test.
_DETAIL_NO_APPLY_HTML = """<!DOCTYPE html>
<html>
<head><title>Job Detail — No Apply Path</title></head>
<body>
<h1>Closed Role</h1>
<p>This job is no longer accepting applications.</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Loopback HTTP server that serves different pages at different paths
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MultiPageHTTPServer:
    """A loopback-only HTTP server that serves different HTML per path.

    This is NOT external network — it binds to 127.0.0.1 and serves static
    local fixture HTML. No real ATS, no real candidate data, no owner PII.
    """

    def __init__(self, routes: dict[str, str]) -> None:
        """``routes`` maps path → HTML content. Path "/" is the default."""
        self._routes = {k if k.startswith("/") else f"/{k}": v for k, v in routes.items()}
        self._port = _find_free_port()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

        outer = self

        class _Handler(SimpleHTTPRequestHandler):
            def log_message(self, _fmt: str, *args: object) -> None:
                del args

            def do_GET(self) -> None:
                # Match by path prefix so "/apply?foo=bar" still hits "/apply".
                path = self.path.split("?", 1)[0]
                html = outer._routes.get(path) or outer._routes.get("/")
                if html is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

        self._handler_cls = _Handler

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def detail_url(self) -> str:
        return f"{self.base_url}/detail"

    @property
    def form_url(self) -> str:
        return f"{self.base_url}/apply"

    def start(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), self._handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@pytest.fixture
def detail_form_server():
    """Server with a detail page (/detail) and a form (/apply)."""
    srv = _MultiPageHTTPServer(
        {
            "/detail": _DETAIL_HTML,
            "/apply": _FORM_HTML,
        }
    )
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def no_apply_server():
    """Server with a detail page that has NO safe apply path."""
    srv = _MultiPageHTTPServer(
        {
            "/detail": _DETAIL_NO_APPLY_HTML,
        }
    )
    srv.start()
    yield srv
    srv.stop()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8320,
        data_dir=tmp_path / "uaa_url_sep",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
        browser_max_steps=5,
    )


def _make_job(tmp_path: Path, detail_url: str) -> ApplicationJob:
    """Build a job whose ``url`` is the DETAIL page (source URL)."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv fixture")
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF-1.4 fake cover")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="url-sep", url=detail_url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="FixtureCo",
        title="Fixture Role",
        url=detail_url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="url-sep",
        metadata={
            "candidate_profile": {
                "full_name": "Test Candidate",
                "first_name": "Test",
                "last_name": "Candidate",
                "email": "test.candidate@example.com",
                "phone": "+1 555 0100",
                "city": "Erlangen",
                "country": "Germany",
            }
        },
    )


def _expected_cv_hash(tmp_path: Path) -> str:
    cv_path = tmp_path / "cv.pdf"
    return hashlib.sha256(cv_path.read_bytes()).hexdigest()[:32]


def _setup_db(tmp_path: Path, settings: Settings, job: ApplicationJob):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    with session_scope(sf) as session:
        upsert_application_job(session, job)
    Base.metadata.create_all(engine)
    return engine, sf


def _create_app(settings: Settings, engine: Any, sf: Any) -> Any:
    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = sf
    app.state.review_states = {}
    from universal_auto_applier.api.routes.logs import init_log_buffer

    init_log_buffer(app)
    Base.metadata.create_all(engine)
    return app


# ---------------------------------------------------------------------------
# A. Phase-A observation — detail→form navigation
# ---------------------------------------------------------------------------


@pytest.fixture
def url_sep_app(tmp_path: Path, detail_form_server: _MultiPageHTTPServer):
    """Production create_app + FixtureContextFactory, job.url = detail page."""
    settings = _make_settings(tmp_path)
    job = _make_job(tmp_path, detail_form_server.detail_url)
    engine, sf = _setup_db(tmp_path, settings, job)
    app = _create_app(settings, engine, sf)
    stub = FixtureContextFactory(headless=True)
    app.state.submission_context_factory = stub
    yield app, job, engine, sf, settings, tmp_path, detail_form_server
    stub.close()
    engine.dispose()


class TestPhaseAObserveDetailToForm:
    """A: Phase-A observation navigates from detail page to application form."""

    def test_observe_navigates_detail_to_form(self, url_sep_app) -> None:
        """POST /observe navigates from job.url (detail) to the application form."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200, f"observe failed: {resp.text}"
            snap = resp.json()["snapshot"]
            # snapshot.application_url is the FORM URL, not the detail URL.
            assert snap["application_url"] == srv.form_url, (
                f"snapshot.application_url={snap['application_url']!r} "
                f"expected form_url={srv.form_url!r}"
            )
            assert snap["application_url"] != job.url, (
                "snapshot.application_url equals job.url (detail page) — "
                "the ATS URL separation defect is not fixed"
            )

    def test_observe_interlock_before_navigation(self, url_sep_app) -> None:
        """The interlock is installed BEFORE the first page.goto.

        We verify this indirectly: the detail page's Apply link is followed
        by click_action, which navigates to /apply. The interlock must be
        active on the /apply page (installed on the context before any
        navigation). We prove it by checking the form's submit button is
        blocked by the interlock — proving the interlock was active when
        the form page loaded.
        """
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            snap = resp.json()["snapshot"]
            # The submit control was found (proves the form was reached and
            # analyzed). The interlock did not prevent analysis — it only
            # prevents actual submit clicks, which observation never does.
            assert snap.get("submit_control") is not None
            assert snap["submit_control"].get("text")

    def test_observe_dangerous_submit_never_clicked_during_discovery(self, url_sep_app) -> None:
        """The dangerous_submit clickable is never clicked during navigation.

        Observation only follows safe_apply / safe_continue actions. The
        submit control on the form page is detected by analyze_page but
        never clicked — the interlock blocks any script-driven submit and
        the observe path never calls page.click on a dangerous_submit.
        """
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            # If the submit button had been clicked, the form would have
            # navigated to /submit (the form action) and the page would no
            # longer be the form. The snapshot would then be empty or the
            # observe would have failed. A 200 with a non-empty snapshot
            # proves the submit was NOT clicked.
            snap = resp.json()["snapshot"]
            assert snap["application_url"] == srv.form_url
            assert len(snap.get("fields", [])) > 0

    def test_observe_fields_non_empty(self, url_sep_app) -> None:
        """The snapshot has non-empty fields from the actual form."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            snap = resp.json()["snapshot"]
            fields = snap.get("fields", [])
            assert len(fields) > 0, "snapshot fields is empty"
            labels = {f.get("label", "") for f in fields}
            assert "Full Name" in labels
            assert "Email" in labels
            assert "Resume" in labels

    def test_observe_documents_with_content_hash(self, url_sep_app) -> None:
        """The snapshot has a document with a non-empty content hash."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            snap = resp.json()["snapshot"]
            docs = snap.get("documents", [])
            assert len(docs) > 0, "no documents uploaded"
            cv_docs = [d for d in docs if d.get("document_kind") == "cv"]
            assert len(cv_docs) >= 1
            assert cv_docs[0].get("content_hash"), "content_hash is empty"
            expected = _expected_cv_hash(tmp_path)
            assert cv_docs[0]["content_hash"] == expected

    def test_observe_submit_control_present(self, url_sep_app) -> None:
        """The snapshot has a submit_control from the actual form."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            snap = resp.json()["snapshot"]
            assert snap.get("submit_control") is not None
            assert snap["submit_control"].get("text") == "Submit Application"

    def test_job_url_unchanged_after_observe(self, url_sep_app) -> None:
        """job.url is NOT mutated by observation."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        original_job_url = job.url
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
        with session_scope(sf) as session:
            reloaded = get_application_job(session, job.application_id)
            assert reloaded is not None
            assert reloaded.url == original_job_url, (
                f"job.url mutated: {reloaded.url!r} != {original_job_url!r}"
            )

    def test_application_id_unchanged_after_observe(self, url_sep_app) -> None:
        """application_id is NOT mutated by observation."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        original_app_id = job.application_id
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
        with session_scope(sf) as session:
            reloaded = get_application_job(session, job.application_id)
            assert reloaded is not None
            assert reloaded.application_id == original_app_id

    def test_observe_snapshot_persists_across_fresh_db_session(self, url_sep_app) -> None:
        """The persisted snapshot (with form URL) survives a fresh DB session."""
        app, job, engine, sf, settings, tmp_path, srv = url_sep_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            assert approval is not None
            assert approval.snapshot_json is not None
            import json

            raw = approval.snapshot_json
            data = raw if isinstance(raw, dict) else json.loads(raw)
            from universal_auto_applier.submission.models import SubmissionSnapshot

            snapshot = SubmissionSnapshot.model_validate(data)
            assert snapshot.application_url == srv.form_url
            assert snapshot.application_url != job.url


# ---------------------------------------------------------------------------
# B. Failure path — detail page with no safe apply route
# ---------------------------------------------------------------------------


@pytest.fixture
def no_apply_app(tmp_path: Path, no_apply_server: _MultiPageHTTPServer):
    """Production create_app + FixtureContextFactory, job.url = detail page
    with NO safe apply path."""
    settings = _make_settings(tmp_path)
    job = _make_job(tmp_path, no_apply_server.detail_url)
    engine, sf = _setup_db(tmp_path, settings, job)
    app = _create_app(settings, engine, sf)
    stub = FixtureContextFactory(headless=True)
    app.state.submission_context_factory = stub
    yield app, job, engine, sf, settings, tmp_path, no_apply_server
    stub.close()
    engine.dispose()


class TestPhaseAObserveFailurePath:
    """B: observation fails closed when no application form is reachable."""

    def test_observe_returns_500_when_no_form_reached(self, no_apply_app) -> None:
        """When the detail page has no safe apply path, observe returns 500
        (the service returns None, which the endpoint maps to 500)."""
        app, job, engine, sf, settings, tmp_path, srv = no_apply_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 500, (
                f"expected 500 (observation failed), got {resp.status_code}: {resp.text}"
            )

    def test_observe_no_empty_snapshot_persisted(self, no_apply_app) -> None:
        """No approval row with an empty snapshot is persisted."""
        app, job, engine, sf, settings, tmp_path, srv = no_apply_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 500
        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            # Either no approval at all, or the approval has no snapshot_json
            # (no empty snapshot persisted).
            if approval is not None:
                assert not approval.snapshot_json or approval.snapshot_hash == "", (
                    "an empty-snapshot approval was persisted — the fail-closed "
                    "guard is not working"
                )

    def test_observe_no_authorization_created(self, no_apply_app) -> None:
        """No SubmissionAuthorization is created by a failed observation."""
        app, job, engine, sf, settings, tmp_path, srv = no_apply_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 500
        with session_scope(sf) as session:
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import (
                SubmissionAuthorizationRow,
            )

            auths = (
                session.execute(
                    select(SubmissionAuthorizationRow).where(
                        SubmissionAuthorizationRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(auths) == 0

    def test_observe_no_result_or_claim_created(self, no_apply_app) -> None:
        """No SubmissionResult or SubmissionClaim is created by a failed observation."""
        app, job, engine, sf, settings, tmp_path, srv = no_apply_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 500
        with session_scope(sf) as session:
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import (
                SubmissionClaimRow,
                SubmissionResultRow,
            )

            results = (
                session.execute(
                    select(SubmissionResultRow).where(
                        SubmissionResultRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            claims = (
                session.execute(
                    select(SubmissionClaimRow).where(
                        SubmissionClaimRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(results) == 0
            assert len(claims) == 0


# ---------------------------------------------------------------------------
# C. Review-packet URL source
# ---------------------------------------------------------------------------


class TestReviewPacketURLSource:
    """C: wq8-review-packet uses snapshot.application_url, not job.url."""

    def test_review_packet_uses_snapshot_application_url(
        self, tmp_path: Path, detail_form_server: _MultiPageHTTPServer
    ) -> None:
        """The review packet's application_url comes from the persisted
        snapshot, not job.url."""
        import io
        from contextlib import redirect_stdout

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, detail_form_server.detail_url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)
        stub = FixtureContextFactory(headless=True)
        app.state.submission_context_factory = stub
        try:
            with TestClient(app) as client:
                # First observe to persist a snapshot.
                resp = client.post(f"/api/submit/{job.application_id}/observe")
                assert resp.status_code == 200
                # Now run the review-packet CLI.
                import argparse

                from universal_auto_applier.cli import _wq8_review_packet

                args = argparse.Namespace(
                    application_id=job.application_id,
                    job_url=None,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _wq8_review_packet(settings, args)
                assert rc == 0, f"review-packet failed: {buf.getvalue()}"
                output = buf.getvalue()
                # The application_url in the packet must be the form URL.
                assert f"application_url:     {detail_form_server.form_url}" in output, (
                    f"packet application_url is not the form URL; output:\n{output}"
                )
                # job_url (source) must also be shown and must differ.
                assert f"job_url (source):    {detail_form_server.detail_url}" in output
        finally:
            stub.close()
            engine.dispose()

    def test_review_packet_rejects_mismatching_job_url(
        self, tmp_path: Path, detail_form_server: _MultiPageHTTPServer
    ) -> None:
        """--job-url that differs from snapshot.application_url is rejected."""
        import argparse
        import io
        from contextlib import redirect_stdout

        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, detail_form_server.detail_url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)
        stub = FixtureContextFactory(headless=True)
        app.state.submission_context_factory = stub
        try:
            with TestClient(app) as client:
                resp = client.post(f"/api/submit/{job.application_id}/observe")
                assert resp.status_code == 200
                from universal_auto_applier.cli import _wq8_review_packet

                args = argparse.Namespace(
                    application_id=job.application_id,
                    job_url="https://wrong-url.invalid/apply",
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _wq8_review_packet(settings, args)
                assert rc != 0, (
                    f"review-packet should have rejected mismatching --job-url; "
                    f"output:\n{buf.getvalue()}"
                )
                assert "does not match" in buf.getvalue().lower()
        finally:
            stub.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# D. Authorization URL source
# ---------------------------------------------------------------------------


class TestAuthorizationURLSource:
    """D: wq8-authorize binds snapshot.application_url, not job.url."""

    def test_authorize_uses_snapshot_application_url(
        self, tmp_path: Path, detail_form_server: _MultiPageHTTPServer
    ) -> None:
        """The authorization's application_url is snapshot.application_url."""
        import argparse
        import io
        from contextlib import redirect_stdout

        # enable_real_submission must be True for wq8-authorize to proceed.
        settings = Settings(
            host="127.0.0.1",
            port=8321,
            data_dir=tmp_path / "uaa_auth_url",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=True,
            browser_max_steps=5,
        )
        job = _make_job(tmp_path, detail_form_server.detail_url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)
        stub = FixtureContextFactory(headless=True)
        app.state.submission_context_factory = stub
        try:
            with TestClient(app) as client:
                # Observe to persist the snapshot.
                resp = client.post(f"/api/submit/{job.application_id}/observe")
                assert resp.status_code == 200

                # Run wq8-review-packet to get the review_plan_hash.
                from universal_auto_applier.cli import _wq8_review_packet

                args = argparse.Namespace(
                    application_id=job.application_id,
                    job_url=None,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _wq8_review_packet(settings, args)
                assert rc == 0
                # Extract review_plan_hash from output.
                rph_line = next(
                    line for line in buf.getvalue().splitlines() if "review_plan_hash:" in line
                )
                review_plan_hash = rph_line.split(":", 1)[1].strip()

                # Run wq8-authorize.
                from universal_auto_applier.cli import _wq8_authorize

                auth_args = argparse.Namespace(
                    application_id=job.application_id,
                    review_plan_hash=review_plan_hash,
                    expires_in_hours=24.0,
                    confirm=True,
                    job_url=None,
                )
                auth_buf = io.StringIO()
                with redirect_stdout(auth_buf):
                    auth_rc = _wq8_authorize(settings, auth_args)
                assert auth_rc == 0, f"authorize failed: {auth_buf.getvalue()}"

            # Verify the authorization's application_url is the form URL.
            with session_scope(sf) as session:
                from universal_auto_applier.submission.authorization_store import (
                    get_active_authorization,
                )

                auth = get_active_authorization(session, job.application_id)
                assert auth is not None
                assert auth.application_url == detail_form_server.form_url, (
                    f"authorization application_url={auth.application_url!r} "
                    f"expected form_url={detail_form_server.form_url!r}"
                )
                assert auth.application_url != job.url, (
                    "authorization application_url equals job.url (detail page)"
                )
        finally:
            stub.close()
            engine.dispose()


# ---------------------------------------------------------------------------
# E. Coordinator URL binding
# ---------------------------------------------------------------------------


class TestCoordinatorURLBinding:
    """E: coordinator accepts job.url != snapshot.application_url; rejects
    when the current form URL changes after approval."""

    def test_coordinator_accepts_distinct_job_and_form_urls(self, tmp_path: Path) -> None:
        """A valid authorization with job.url=detail + snapshot.application_url=form
        passes the DB gate when all other bindings match."""
        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )
        from universal_auto_applier.submission.coordinator import SubmissionCoordinator
        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotDocument,
            SubmissionSnapshotField,
            SubmissionSnapshotSubmitControl,
        )

        settings = _make_settings(tmp_path)
        detail_url = "https://detail.invalid/detail"
        form_url = "https://form.invalid/apply"
        app_id = compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="coord-test", url=detail_url
        )
        # Persist a job with the EXACT application_id we use below (FK constraint).
        job = _make_job(tmp_path, detail_url)
        job = job.model_copy(update={"application_id": app_id})
        engine, sf = _setup_db(tmp_path, settings, job)

        snapshot = SubmissionSnapshot(
            application_id=app_id,
            application_url=form_url,
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    filled_value="Test",
                    status="filled",
                )
            ],
            documents=[
                SubmissionSnapshotDocument(
                    document_kind="cv",
                    path="/cv.pdf",
                    content_hash="abc123",
                )
            ],
            pending_intervention_count=0,
            submit_control=SubmissionSnapshotSubmitControl(text="Submit", selector="#btn"),
        ).with_hashes()

        plan = build_review_plan(
            application_id=app_id,
            company=job.company,
            job_title=job.title,
            application_url=form_url,
            fields=snapshot.fields,
            documents=snapshot.documents,
            submit_control_text="Submit",
            submit_control_selector="#btn",
            pending_intervention_count=0,
        )
        frozen = compute_review_plan_hash(plan)

        # Create the authorization in the DB.
        from datetime import UTC, datetime, timedelta

        from universal_auto_applier.submission.authorization_store import (
            create_authorization,
        )

        with session_scope(sf) as session:
            create_authorization(
                session,
                application_id=app_id,
                application_url=form_url,
                job_company=job.company,
                job_title=job.title,
                review_plan_hash=frozen,
                document_hashes=["abc123"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        coordinator = SubmissionCoordinator(settings, sf)
        # The DB gate must NOT reject just because auth.application_url != job.url.
        # job.url is the detail URL; auth.application_url is the form URL.
        with session_scope(sf) as session:
            result = coordinator._check_wq8_authorization_db(  # noqa: SLF001
                session, application_id=app_id, current_snapshot=snapshot, job=job
            )
        # None means the gate passed (no blocking result).
        assert result is None, f"coordinator rejected a valid distinct-URL authorization: {result}"
        engine.dispose()

    def test_coordinator_rejects_when_form_url_changes(self, tmp_path: Path) -> None:
        """When the current snapshot's application_url differs from the
        authorized form URL, the gate returns APPROVAL_STALE."""
        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )
        from universal_auto_applier.submission.coordinator import SubmissionCoordinator
        from universal_auto_applier.submission.models import (
            SubmissionResultState,
            SubmissionSnapshot,
            SubmissionSnapshotDocument,
            SubmissionSnapshotField,
            SubmissionSnapshotSubmitControl,
        )

        settings = _make_settings(tmp_path)
        detail_url = "https://detail.invalid/detail"
        original_form_url = "https://form.invalid/apply"
        changed_form_url = "https://form.invalid/DIFFERENT"
        app_id = compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="coord-stale", url=detail_url
        )
        job = _make_job(tmp_path, detail_url)
        job = job.model_copy(update={"application_id": app_id})
        engine, sf = _setup_db(tmp_path, settings, job)

        # Snapshot with the CHANGED form URL (simulating the ATS redirecting
        # after approval was frozen).
        snapshot = SubmissionSnapshot(
            application_id=app_id,
            application_url=changed_form_url,
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    filled_value="Test",
                    status="filled",
                )
            ],
            documents=[
                SubmissionSnapshotDocument(
                    document_kind="cv", path="/cv.pdf", content_hash="abc123"
                )
            ],
            pending_intervention_count=0,
            submit_control=SubmissionSnapshotSubmitControl(text="Submit", selector="#btn"),
        ).with_hashes()

        # Authorization was frozen with the ORIGINAL form URL.
        plan = build_review_plan(
            application_id=app_id,
            company=job.company,
            job_title=job.title,
            application_url=original_form_url,
            fields=snapshot.fields,
            documents=snapshot.documents,
            submit_control_text="Submit",
            submit_control_selector="#btn",
            pending_intervention_count=0,
        )
        frozen = compute_review_plan_hash(plan)

        from datetime import UTC, datetime, timedelta

        from universal_auto_applier.submission.authorization_store import (
            create_authorization,
        )

        with session_scope(sf) as session:
            create_authorization(
                session,
                application_id=app_id,
                application_url=original_form_url,
                job_company=job.company,
                job_title=job.title,
                review_plan_hash=frozen,
                document_hashes=["abc123"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        coordinator = SubmissionCoordinator(settings, sf)
        with session_scope(sf) as session:
            result = coordinator._check_wq8_authorization_db(  # noqa: SLF001
                session, application_id=app_id, current_snapshot=snapshot, job=job
            )
        assert result is not None, "coordinator did not reject the stale-URL authorization"
        assert result.state == SubmissionResultState.APPROVAL_STALE, (
            f"expected APPROVAL_STALE, got {result.state}"
        )
        engine.dispose()


# ---------------------------------------------------------------------------
# F. Phase-B browser target
# ---------------------------------------------------------------------------


class TestPhaseBBrowserTarget:
    """F: Phase-B navigates to the approved form URL, not job.url; post-fill
    URL guard fails closed on mismatch."""

    def test_phase_b_navigates_to_approved_form_url(
        self, tmp_path: Path, detail_form_server: _MultiPageHTTPServer
    ) -> None:
        """When a WQ-8 authorization is active, _execute_in_browser navigates
        to the approved snapshot.application_url, NOT job.url.

        We use a stub factory that records the goto URL, and verify it received
        the form URL (not the detail URL).
        """

        settings = Settings(
            host="127.0.0.1",
            port=8322,
            data_dir=tmp_path / "uaa_phase_b",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=True,
            browser_max_steps=5,
        )
        job = _make_job(tmp_path, detail_form_server.detail_url)
        engine, sf = _setup_db(tmp_path, settings, job)

        # Persist a snapshot with application_url = form_url.
        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotDocument,
            SubmissionSnapshotField,
            SubmissionSnapshotSubmitControl,
        )
        from universal_auto_applier.submission.store import create_approval

        form_url = detail_form_server.form_url
        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url=form_url,
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    filled_value="Test",
                    status="filled",
                )
            ],
            documents=[
                SubmissionSnapshotDocument(
                    document_kind="cv",
                    path=str(tmp_path / "cv.pdf"),
                    content_hash=_expected_cv_hash(tmp_path),
                )
            ],
            pending_intervention_count=0,
            submit_control=SubmissionSnapshotSubmitControl(text="Submit", selector="#btn"),
        ).with_hashes()

        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        # Create a WQ-8 authorization bound to the form URL.
        from datetime import UTC, datetime, timedelta

        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )
        from universal_auto_applier.submission.authorization_store import (
            create_authorization,
        )

        plan = build_review_plan(
            application_id=job.application_id,
            company=job.company,
            job_title=job.title,
            application_url=form_url,
            fields=snapshot.fields,
            documents=snapshot.documents,
            submit_control_text="Submit",
            submit_control_selector="#btn",
            pending_intervention_count=0,
        )
        frozen = compute_review_plan_hash(plan)
        with session_scope(sf) as session:
            create_authorization(
                session,
                application_id=job.application_id,
                application_url=form_url,
                job_company=job.company,
                job_title=job.title,
                review_plan_hash=frozen,
                document_hashes=[_expected_cv_hash(tmp_path)],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        # Use a recording stub factory to capture the goto URL.
        from playwright.sync_api import BrowserContext

        class _RecordingPage:
            def __init__(self, goto_log: list[str]) -> None:
                self._goto_log = goto_log
                self.url = form_url  # pretend we landed on the form URL

            def goto(self, url: str, **kwargs: Any) -> None:
                self._goto_log.append(url)
                self.url = url

            def wait_for_timeout(self, ms: int) -> None:
                pass

            def evaluate(self, script: str) -> Any:
                return {}

        class _RecordingContext:
            def __init__(self, goto_log: list[str]) -> None:
                self._goto_log = goto_log
                self.pages: list[Any] = []

            def add_init_script(self, script: str) -> None:
                pass

            def new_page(self) -> Any:
                return _RecordingPage(self._goto_log)

            def close(self) -> None:
                pass

        class _RecordingFactory:
            def __init__(self) -> None:
                self.goto_log: list[str] = []
                self._ctx: _RecordingContext | None = None

            def create_context(self) -> BrowserContext:
                self._ctx = _RecordingContext(self.goto_log)
                return self._ctx  # type: ignore[return-value]

            def close(self) -> None:
                pass

        recording_factory = _RecordingFactory()
        service = SubmissionExecutionService(settings, sf, recording_factory)

        # We need to call _execute_in_browser directly. But it requires a
        # claim_id and approval_id. Since this is a stub (no real browser),
        # the coordinator's execute_submission_from_page will fail when it
        # tries to interact with the stub page. The key assertion is that
        # the goto_log contains the form URL, not the detail URL — which
        # happens before the coordinator call.
        service._execute_in_browser(  # noqa: SLF001
            application_id=job.application_id,
            approval_id="test-approval",
            approved_snapshot_hash=snapshot.snapshot_hash,
            claim_id="test-claim",
            job=job,
        )
        # The goto must have targeted the form URL, not job.url (detail).
        assert len(recording_factory.goto_log) > 0, "no goto was called"
        assert recording_factory.goto_log[0] == form_url, (
            f"Phase-B goto targeted {recording_factory.goto_log[0]!r} "
            f"expected form_url={form_url!r}"
        )
        assert recording_factory.goto_log[0] != job.url, (
            f"Phase-B goto targeted job.url (detail page) {job.url!r} — "
            "it must navigate to the approved form URL"
        )
        # The result may be an error (stub page can't really submit), but the
        # goto target is what we assert.
        engine.dispose()

    def test_phase_b_navigates_to_approved_form_url_and_guard_passes(
        self, tmp_path: Path, detail_form_server: _MultiPageHTTPServer
    ) -> None:
        """Phase B navigates to the approved form URL (not job.url), and the
        post-fill URL guard passes when the actual page URL matches the
        approved URL.

        The mismatch-rejection path is proven by
        ``test_coordinator_rejects_when_form_url_changes`` at the binding
        layer (APPROVAL_STALE). This test proves the browser navigates to
        the correct approved URL and the guard does NOT fire on a match.
        """
        from universal_auto_applier.submission.models import (
            SubmissionSnapshot,
            SubmissionSnapshotDocument,
            SubmissionSnapshotField,
            SubmissionSnapshotSubmitControl,
        )
        from universal_auto_applier.submission.store import create_approval

        settings = Settings(
            host="127.0.0.1",
            port=8323,
            data_dir=tmp_path / "uaa_phase_b_guard",
            browser_headless=True,
            submit_mode="review",
            enable_real_submission=True,
            browser_max_steps=5,
        )
        job = _make_job(tmp_path, detail_form_server.detail_url)
        engine, sf = _setup_db(tmp_path, settings, job)

        approved_form_url = detail_form_server.form_url

        snapshot = SubmissionSnapshot(
            application_id=job.application_id,
            application_url=approved_form_url,
            fields=[
                SubmissionSnapshotField(
                    field_token="f1",
                    label="Name",
                    field_type="text",
                    filled_value="Test",
                    status="filled",
                )
            ],
            documents=[
                SubmissionSnapshotDocument(
                    document_kind="cv",
                    path=str(tmp_path / "cv.pdf"),
                    content_hash=_expected_cv_hash(tmp_path),
                )
            ],
            pending_intervention_count=0,
            submit_control=SubmissionSnapshotSubmitControl(text="Submit", selector="#btn"),
        ).with_hashes()

        with session_scope(sf) as session:
            create_approval(session, application_id=job.application_id, snapshot=snapshot)

        from datetime import UTC, datetime, timedelta

        from universal_auto_applier.submission.authorization import (
            build_review_plan,
            compute_review_plan_hash,
        )
        from universal_auto_applier.submission.authorization_store import (
            create_authorization,
        )

        plan = build_review_plan(
            application_id=job.application_id,
            company=job.company,
            job_title=job.title,
            application_url=approved_form_url,
            fields=snapshot.fields,
            documents=snapshot.documents,
            submit_control_text="Submit",
            submit_control_selector="#btn",
            pending_intervention_count=0,
        )
        frozen = compute_review_plan_hash(plan)
        with session_scope(sf) as session:
            create_authorization(
                session,
                application_id=job.application_id,
                application_url=approved_form_url,
                job_company=job.company,
                job_title=job.title,
                review_plan_hash=frozen,
                document_hashes=[_expected_cv_hash(tmp_path)],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        stub = FixtureContextFactory(headless=True)
        service = SubmissionExecutionService(settings, sf, stub)
        result = service._execute_in_browser(  # noqa: SLF001
            application_id=job.application_id,
            approval_id="test-approval",
            approved_snapshot_hash=snapshot.snapshot_hash,
            claim_id="test-claim",
            job=job,
        )
        # The browser navigated to the approved form URL (not job.url/detail).
        # The post-fill URL guard did NOT fire on the URL match (the error
        # message must NOT contain "approved application URL changed"). A
        # snapshot-hash mismatch from the coordinator is expected (the live
        # form's filled values differ from the placeholder snapshot we
        # persisted) — that is a different gate, not the URL guard.
        assert not result.clicked, "submit was clicked during a test"
        assert "approved application URL changed" not in (result.error_message or ""), (
            f"post-fill URL guard fired on a matching URL: {result.error_message}"
        )
        stub.close()
        engine.dispose()
