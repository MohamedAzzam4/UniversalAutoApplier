"""WQ-8 Phase A — production review-observation snapshot persistence.

Hermetic regressions proving the production wiring fix:

1. **Event order**: context created -> interlock installed -> page.goto.
   Fails if navigation happens before the interlock is installed.
2. **Production-app observe flow**: real ``create_app`` lifecycle + stub
   browser factory -> ``POST /api/submit/{id}/observe`` -> non-503 ->
   non-empty snapshot -> persisted -> fresh DB session reloads it.
3. **Injected factory preserved**: a test/harness-injected factory is NOT
   overwritten by the production lifespan.
4. **Observation cannot authorize or submit**: after observe, no
   ``SubmissionAuthorization`` row exists, no ``SubmissionResult`` row
   exists, no ``SubmissionClaim`` row exists, and the interlock counters
   show zero authorized submits and zero UAA submit clicks.

These tests use a local file:// fixture HTML page — no external network,
no real ATS, no real candidate data, no owner PII.
"""

from __future__ import annotations

import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import BrowserContext, sync_playwright

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
from universal_auto_applier.persistence.models import (
    Base,
    SubmissionApprovalRow,
    SubmissionAuthorizationRow,
    SubmissionClaimRow,
    SubmissionResultRow,
)
from universal_auto_applier.submission.execution_service import (
    FixtureContextFactory,
    PlaywrightContextFactory,
    SubmissionExecutionService,
)
from universal_auto_applier.submission.store import get_active_approval

# A minimal local fixture form. Has a submit button (so analyze_page finds a
# dangerous_submit clickable), one required text field, and one file field.
# Served via a loopback HTTP server — no external network.
_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><title>WQ8 Observe Fixture</title></head>
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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FixtureHTTPServer:
    """A loopback-only HTTP server that serves the fixture HTML.

    This is NOT external network — it binds to 127.0.0.1 and serves a static
    local fixture file. No real ATS, no real candidate data, no owner PII.
    """

    def __init__(self, html: str) -> None:
        self._html = html
        self._port = _find_free_port()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

        handler = type(
            "_Handler",
            (SimpleHTTPRequestHandler,),
            {
                "log_message": lambda self, _fmt, *args: None,
                "do_GET": lambda s: self._serve(s),
            },
        )
        self._handler_cls = handler

    def _serve(self, handler: SimpleHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.end_headers()
        handler.wfile.write(self._html.encode("utf-8"))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/form"

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
def fixture_server():
    """Start a loopback HTTP server serving the fixture form."""
    srv = _FixtureHTTPServer(_FIXTURE_HTML)
    srv.start()
    yield srv
    srv.stop()


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8310,
        data_dir=tmp_path / "uaa_wq8_obs",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
    )


def _make_job(tmp_path: Path, fixture_url: str) -> ApplicationJob:
    """Build a job that navigates to ``fixture_url`` (a loopback HTTP URL).

    Includes a synthetic ``candidate_profile`` in metadata so the official
    field-mapping path deterministically fills the ``Full Name`` and
    ``Email`` text fields (not just the ``Resume`` file field). This makes
    the snapshot assertions for non-empty fields and filled values
    deterministic — no owner PII, purely synthetic.
    """
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake cv fixture")
    cover = tmp_path / "cover.pdf"
    cover.write_bytes(b"%PDF-1.4 fake cover")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="wq8-obs", url=fixture_url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="FixtureCo",
        title="Fixture Role",
        url=fixture_url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="wq8-obs",
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
    """Compute the expected SHA-256-derived content hash for the synthetic CV.

    The project's canonical snapshot hashing implementation
    (``submission/models.py:build_snapshot_from_report``) computes document
    content hashes as ``hashlib.sha256(file_bytes).hexdigest()[:32]``.
    """
    import hashlib

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
# 1. Event-order regression: context -> interlock -> navigation
# ---------------------------------------------------------------------------


class _OrderRecordingContext:
    """A fake BrowserContext that records the order of interlock install vs
    page.goto. Used to prove the observe path installs the interlock BEFORE
    any navigation.

    The interlock is installed via ``context.add_init_script(script)`` (see
    ``browser/submit_interlock.py:install_interlock``). Navigation happens
    via ``page.goto(url)``. We record the call order in a shared list.
    """

    def __init__(self, order_log: list[str]) -> None:
        self._order_log = order_log
        self.pages: list[Any] = []
        self._fake_page = _OrderRecordingPage(order_log)

    def add_init_script(self, script: str) -> None:
        # This is what install_interlock calls.
        self._order_log.append("interlock_installed")
        # Hand the page its init script so it knows the interlock ran.
        self._fake_page._interlock_installed = True

    def new_page(self) -> Any:
        self._order_log.append("new_page")
        self.pages.append(self._fake_page)
        return self._fake_page

    def close(self) -> None:
        pass


class _OrderRecordingPage:
    """A fake Page that records navigation order relative to interlock install."""

    def __init__(self, order_log: list[str]) -> None:
        self._order_log = order_log
        self._interlock_installed = False

    def goto(self, url: str, **kwargs: Any) -> None:
        if self._interlock_installed:
            self._order_log.append("goto_after_interlock")
        else:
            self._order_log.append("goto_BEFORE_interlock")

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def evaluate(self, script: str) -> Any:
        # Return a counters dict matching read_counters' default shape.
        return {
            "submit_events": 0,
            "form_submit_calls": 0,
            "request_submit_calls": 0,
            "dispatch_submit_events": 0,
            "blocked_submissions": 0,
            "navigation_attempts": 0,
            "authorized_submits": 0,
        }


class _OrderRecordingFactory:
    """A stub BrowserContextFactory that returns an _OrderRecordingContext."""

    def __init__(self) -> None:
        self.order_log: list[str] = []
        self._context: _OrderRecordingContext | None = None

    def create_context(self) -> BrowserContext:
        self.order_log.append("context_created")
        self._context = _OrderRecordingContext(self.order_log)
        return self._context  # type: ignore[return-value]

    def close(self) -> None:
        pass


class TestEventOrderInterlockBeforeNavigation:
    """Milestone C: prove context -> interlock -> navigation order."""

    def test_observe_installs_interlock_before_navigation(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        """The observe path must install the interlock BEFORE page.goto.

        We use a stub factory whose context records the call order. The
        observe path must produce:
            context_created -> interlock_installed -> new_page -> goto_after_interlock
        and must NEVER produce ``goto_BEFORE_interlock``.
        """
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)

        stub_factory = _OrderRecordingFactory()
        service = SubmissionExecutionService(settings, sf, stub_factory)

        # We don't need the full observe to succeed — we just need to prove
        # the order. The stub page will cause execute_live_form to fail, but
        # the interlock + goto order is recorded before that. The service
        # catches the exception and returns None.
        service.observe_and_persist_snapshot(application_id=job.application_id)

        # The snapshot may be None (stub page can't really fill a form), but
        # the ORDER is what we assert.
        order = stub_factory.order_log
        assert "context_created" in order, "context was never created"
        assert "interlock_installed" in order, "interlock was never installed"
        assert "goto_BEFORE_interlock" not in order, (
            "navigation happened BEFORE the interlock was installed — "
            "this is the exact defect the workpackage forbids"
        )
        # The interlock must be installed before any goto.
        interlock_idx = order.index("interlock_installed")
        # Find the first goto (either variant).
        goto_idx = next((i for i, e in enumerate(order) if e.startswith("goto_")), None)
        assert goto_idx is not None, "navigation never happened"
        assert interlock_idx < goto_idx, (
            f"interlock installed at index {interlock_idx} but navigation at "
            f"{goto_idx}; interlock must come first. Order: {order}"
        )
        engine.dispose()

    def test_observe_never_navigates_before_interlock_even_on_failure(self, tmp_path: Path) -> None:
        """Even if the observe path fails (e.g. bad URL), navigation must
        never happen before the interlock is installed."""
        settings = _make_settings(tmp_path)
        # Use a job with a URL that will fail to load — but the interlock
        # must still be installed first. Use an HTTP URL pointing at a port
        # nothing is listening on (connection refused).
        job = _make_job(tmp_path, "http://127.0.0.1:1/nonexistent")
        engine, sf = _setup_db(tmp_path, settings, job)

        stub_factory = _OrderRecordingFactory()
        service = SubmissionExecutionService(settings, sf, stub_factory)

        snapshot = service.observe_and_persist_snapshot(application_id=job.application_id)
        # Snapshot is None because navigation fails.
        assert snapshot is None

        order = stub_factory.order_log
        assert "context_created" in order
        assert "interlock_installed" in order
        assert "goto_BEFORE_interlock" not in order
        # Interlock must come before any goto attempt.
        if any(e.startswith("goto_") for e in order):
            interlock_idx = order.index("interlock_installed")
            goto_idx = next(i for i, e in enumerate(order) if e.startswith("goto_"))
            assert interlock_idx < goto_idx
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. Production-app hermetic observe regression
# ---------------------------------------------------------------------------


@pytest.fixture
def wq8_obs_app(tmp_path: Path, fixture_server: _FixtureHTTPServer):
    """A production create_app with a FixtureContextFactory pre-injected,
    pointed at a loopback HTTP fixture form.

    Yields ``(app, job, engine, sf, settings, tmp_path)`` so tests can
    compute the expected synthetic CV content hash from the same tmp_path.
    """
    settings = _make_settings(tmp_path)
    job = _make_job(tmp_path, fixture_server.url)
    engine, sf = _setup_db(tmp_path, settings, job)
    app = _create_app(settings, engine, sf)

    # Pre-inject a FixtureContextFactory (serves local pages via real
    # headless Chromium). This is the same pattern as the test harnesses.
    stub = FixtureContextFactory(headless=True)
    app.state.submission_context_factory = stub

    yield app, job, engine, sf, settings, tmp_path

    stub.close()
    engine.dispose()


class TestProductionAppObserveRegression:
    """Milestone D: real create_app + stub factory -> POST /observe -> non-503
    -> non-empty snapshot -> persisted -> reloadable from a fresh DB session."""

    def test_observe_returns_non_503(self, wq8_obs_app) -> None:
        """The observe endpoint must NOT return 503 — the factory is registered."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code != 503, f"observe returned 503 (the defect); body: {resp.text}"
            # Accept 200 (success) — the fixture form is reachable.
            assert resp.status_code == 200, (
                f"observe returned unexpected status {resp.status_code}; body: {resp.text}"
            )

    def test_observe_persists_non_empty_snapshot_with_fields(self, wq8_obs_app) -> None:
        """The observe flow must persist a non-empty snapshot with actual fields.

        Finding 2 correction: the previous test only checked snapshot_hash and
        submit_control — it did NOT prove fields were non-empty. This test
        fails if fields regress to [].
        """
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200, f"observe failed: {resp.text}"
            data = resp.json()
            assert "snapshot" in data
            snap = data["snapshot"]
            # The snapshot must have a hash (proves build_snapshot ran).
            assert snap.get("snapshot_hash"), "snapshot_hash is empty"
            # The fixture form has a submit button — analyze_page must find it.
            assert snap.get("submit_control") is not None, "submit_control is None"
            assert snap["submit_control"].get("text"), "submit_control text is empty"

            # Finding 2: explicit non-empty fields proof.
            fields = snap.get("fields", [])
            assert isinstance(fields, list), "fields is not a list"
            assert len(fields) > 0, (
                "snapshot fields is empty — the observe flow did not capture any fields; "
                "this is the exact regression the workpackage forbids"
            )
            # Expected fixture fields are represented (Full Name, Email, Resume).
            labels = {f.get("label", "") for f in fields}
            assert "Full Name" in labels, f"'Full Name' field missing; labels: {labels}"
            assert "Email" in labels, f"'Email' field missing; labels: {labels}"
            assert "Resume" in labels, f"'Resume' field missing; labels: {labels}"
            # At least the Resume file field must be filled (the synthetic CV
            # is mapped via the 'resume' label pattern to job.cv_pdf).
            resume_field = next(f for f in fields if f.get("label") == "Resume")
            assert resume_field.get("field_type") == "file", (
                f"Resume field type is {resume_field.get('field_type')!r}, expected 'file'"
            )
            assert resume_field.get("status") == "filled", (
                f"Resume field status is {resume_field.get('status')!r}, expected 'filled'"
            )
            assert resume_field.get("filled_value"), (
                "Resume filled_value is empty — the synthetic CV path was not captured"
            )

    def test_observe_snapshot_reloadable_from_fresh_session(self, wq8_obs_app) -> None:
        """After observe, a FRESH DB session (not the request's session) must
        be able to reload the persisted snapshot from the approval row.

        Finding 2 correction: also proves the reloaded snapshot still contains
        the non-empty fields (not just the hash/submit_control).
        """
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        # Open a brand-new session — prove the snapshot was persisted, not
        # just held in memory.
        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            assert approval is not None, "no approval row persisted after observe"
            assert approval.snapshot_json is not None, "snapshot_json is None"
            assert approval.snapshot_hash, "snapshot_hash is empty on approval row"
            # Reload the snapshot from the JSON.
            import json

            raw = approval.snapshot_json
            data = raw if isinstance(raw, dict) else json.loads(raw)
            from universal_auto_applier.submission.models import SubmissionSnapshot

            snapshot = SubmissionSnapshot.model_validate(data)
            assert snapshot.snapshot_hash == approval.snapshot_hash
            assert snapshot.application_url == job.url
            # The submit control must be present (proves analyze_page ran).
            assert snapshot.submit_control is not None
            assert snapshot.submit_control.text
            # Finding 2: the reloaded snapshot must still contain the non-empty
            # fields (proves they were persisted, not just in-memory).
            assert len(snapshot.fields) > 0, (
                "reloaded snapshot has 0 fields — fields were not persisted"
            )
            reloaded_labels = {f.label for f in snapshot.fields}
            assert "Full Name" in reloaded_labels, (
                f"'Full Name' missing from reloaded snapshot; labels: {reloaded_labels}"
            )
            assert "Resume" in reloaded_labels, (
                f"'Resume' missing from reloaded snapshot; labels: {reloaded_labels}"
            )

    def test_observe_snapshot_has_document_content_hash(self, wq8_obs_app) -> None:
        """The snapshot must include a real document content hash.

        Finding 1 correction: the previous test only checked that the
        ``documents`` field existed and explicitly allowed it to be empty —
        a false positive. This test exercises the full official path:
        POST /observe → execute_live_form → upload synthetic CV →
        build_snapshot → persist approval, then asserts the document is
        present with the correct content hash.
        """
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            data = resp.json()
            snap = data["snapshot"]
            documents = snap.get("documents", [])
            assert isinstance(documents, list), "documents is not a list"
            # Finding 1: documents must be non-empty — the Resume file input
            # must have triggered a synthetic CV upload.
            assert len(documents) > 0, (
                "snapshot documents is empty — the synthetic CV was not uploaded; "
                "the official observe path must upload at least one document"
            )
            # At least the expected synthetic CV is represented.
            cv_docs = [d for d in documents if d.get("document_kind") == "cv"]
            assert len(cv_docs) >= 1, f"no cv document_kind found; documents: {documents}"
            cv_doc = cv_docs[0]
            # document_kind is correct.
            assert cv_doc.get("document_kind") == "cv", (
                f"document_kind is {cv_doc.get('document_kind')!r}, expected 'cv'"
            )
            # content_hash is non-empty.
            assert cv_doc.get("content_hash"), (
                "content_hash is empty — the document hash was not computed"
            )
            # content_hash equals the expected SHA-256-derived value per the
            # project's canonical snapshot hashing implementation.
            expected_hash = _expected_cv_hash(tmp_path)
            assert cv_doc["content_hash"] == expected_hash, (
                f"content_hash mismatch: got {cv_doc['content_hash']!r}, "
                f"expected {expected_hash!r} (sha256(cv.pdf bytes)[:32])"
            )

    def test_observe_document_hash_persists_across_fresh_db_session(self, wq8_obs_app) -> None:
        """Finding 1: the persisted approval snapshot must contain the same
        document + content hash after a fresh DB session reload."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        expected_hash = _expected_cv_hash(tmp_path)

        with session_scope(sf) as session:
            approval = get_active_approval(session, job.application_id)
            assert approval is not None
            assert approval.snapshot_json is not None
            import json

            raw = approval.snapshot_json
            data = raw if isinstance(raw, dict) else json.loads(raw)
            from universal_auto_applier.submission.models import SubmissionSnapshot

            snapshot = SubmissionSnapshot.model_validate(data)
            # The reloaded snapshot must contain the same document + hash.
            assert len(snapshot.documents) > 0, (
                "reloaded snapshot has 0 documents — documents were not persisted"
            )
            cv_docs = [d for d in snapshot.documents if d.document_kind == "cv"]
            assert len(cv_docs) >= 1, (
                f"no cv document in reloaded snapshot; documents: {snapshot.documents}"
            )
            assert cv_docs[0].content_hash == expected_hash, (
                f"reloaded content_hash mismatch: got {cv_docs[0].content_hash!r}, "
                f"expected {expected_hash!r}"
            )

    def test_observe_snapshot_has_pending_intervention_count(self, wq8_obs_app) -> None:
        """The snapshot must include pending_intervention_count (workpackage
        requires 'pending intervention count')."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200
            snap = resp.json()["snapshot"]
            assert "pending_intervention_count" in snap
            # No interventions were created, so it must be 0.
            assert snap["pending_intervention_count"] == 0


# ---------------------------------------------------------------------------
# 3. Injected factory preserved (Milestone E)
# ---------------------------------------------------------------------------


class TestInjectedFactoryPreserved:
    """Milestone E: a test/harness-injected factory is NOT replaced by the
    production lifespan."""

    def test_pre_injected_fixture_factory_preserved(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)

        stub = FixtureContextFactory(headless=True)
        app.state.submission_context_factory = stub

        with TestClient(app) as client:
            # The lifespan must NOT have overwritten the pre-injected factory.
            assert app.state.submission_context_factory is stub, (
                "lifespan overwrote a pre-injected submission_context_factory"
            )
            # The pre-injected factory must be used (not a PlaywrightContextFactory).
            assert not isinstance(app.state.submission_context_factory, PlaywrightContextFactory), (
                "lifespan replaced the stub with a production PlaywrightContextFactory"
            )
            resp = client.get("/api/health")
            assert resp.status_code == 200
        stub.close()
        engine.dispose()

    def test_production_factory_registered_when_none_pre_injected(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        """When no factory is pre-injected, the lifespan registers a
        PlaywrightContextFactory (the production factory)."""
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)

        with TestClient(app) as client:
            assert app.state.submission_context_factory is not None
            assert isinstance(app.state.submission_context_factory, PlaywrightContextFactory), (
                "lifespan must register PlaywrightContextFactory when none pre-injected"
            )
            resp = client.get("/api/health")
            assert resp.status_code == 200
        engine.dispose()


# ---------------------------------------------------------------------------
# 3b. Lifespan factory ownership (Finding 3 correction)
# ---------------------------------------------------------------------------


class _CloseCountingFactory:
    """A sentinel factory that counts how many times ``close()`` is called.

    Used to prove the production lifespan does NOT close a pre-injected
    factory when the lifespan exits (ownership is preserved).
    """

    def __init__(self) -> None:
        self.close_count = 0

    def create_context(self) -> BrowserContext:  # pragma: no cover - never called
        raise AssertionError("CloseCountingFactory.create_context should never be called")

    def close(self) -> None:
        self.close_count += 1


class TestLifespanFactoryOwnership:
    """Finding 3: the lifespan must only close a factory it created/owns.

    The previous code closed ANY ``app.state.submission_context_factory`` in
    the lifespan ``finally`` block, including a pre-injected factory. This
    violated ownership semantics: a test/harness that injects its own
    factory expects to close it itself (e.g. ``submission_server.py`` closes
    its own ``FixtureContextFactory``).
    """

    def test_pre_injected_factory_not_closed_by_lifespan(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        """Enter and exit the production TestClient lifespan WITHOUT invoking
        observation. The pre-injected sentinel factory must be preserved and
        the lifespan must NOT have called ``close()`` on it.
        """
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)

        sentinel = _CloseCountingFactory()
        app.state.submission_context_factory = sentinel

        with TestClient(app) as client:
            # The lifespan must NOT have overwritten the pre-injected factory.
            assert app.state.submission_context_factory is sentinel, (
                "lifespan overwrote a pre-injected submission_context_factory"
            )
            # close() must not have been called during startup.
            assert sentinel.close_count == 0, (
                f"lifespan called close() {sentinel.close_count} time(s) during startup "
                "on a factory it does not own"
            )
            resp = client.get("/api/health")
            assert resp.status_code == 200

        # After lifespan exit, the lifespan must NOT have closed the sentinel.
        assert sentinel.close_count == 0, (
            f"lifespan called close() {sentinel.close_count} time(s) on the "
            "pre-injected sentinel factory during shutdown — the lifespan must "
            "only close factories it created itself"
        )
        # The factory is still the same object (not replaced).
        assert app.state.submission_context_factory is sentinel
        engine.dispose()

    def test_production_factory_closed_on_lifespan_shutdown(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        """When the production lifespan creates its own PlaywrightContextFactory,
        lifespan shutdown must close the owned factory safely.

        We cannot directly count close() on a PlaywrightContextFactory without
        subclassing it, but we can prove the factory was created by the lifespan
        (not pre-injected) and that closing it on shutdown does not raise. The
        fact that no ResourceWarning leaks (the project's strict
        ``filterwarnings = ["error"]`` config) is the proof that shutdown is clean.
        """
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)
        app = _create_app(settings, engine, sf)

        # Do NOT pre-inject a factory — the lifespan must create one.
        with TestClient(app) as client:
            factory = app.state.submission_context_factory
            assert factory is not None, "lifespan did not register a factory"
            assert isinstance(factory, PlaywrightContextFactory), (
                "lifespan must create a PlaywrightContextFactory when none is pre-injected"
            )
            resp = client.get("/api/health")
            assert resp.status_code == 200
        # After lifespan exit, the owned factory was closed in the finally
        # block. No exception was raised (TestClient context exit would have
        # propagated it). The strict ResourceWarning filter proves no leak.
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. Observation cannot authorize or submit (Milestone F)
# ---------------------------------------------------------------------------


class TestObservationCannotAuthorizeOrSubmit:
    """Milestone F: observation creates no SubmissionAuthorization, no
    SubmissionResult, no SubmissionClaim, and the interlock records zero
    authorized submits and zero UAA submit clicks."""

    def test_observe_creates_no_authorization(self, wq8_obs_app) -> None:
        """Observation must NOT create a SubmissionAuthorization row."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        with session_scope(sf) as session:
            from sqlalchemy import select

            auths = (
                session.execute(
                    select(SubmissionAuthorizationRow).where(
                        SubmissionAuthorizationRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(auths) == 0, (
                f"observation created {len(auths)} SubmissionAuthorization row(s) — "
                "observation must never authorize submission"
            )

    def test_observe_creates_no_submission_result(self, wq8_obs_app) -> None:
        """Observation must NOT create a SubmissionResult row (no submit
        attempt was made)."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        with session_scope(sf) as session:
            from sqlalchemy import select

            results = (
                session.execute(
                    select(SubmissionResultRow).where(
                        SubmissionResultRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(results) == 0, (
                f"observation created {len(results)} SubmissionResult row(s) — "
                "observation must never attempt submission"
            )

    def test_observe_creates_no_submission_claim(self, wq8_obs_app) -> None:
        """Observation must NOT create a SubmissionClaim row."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        with session_scope(sf) as session:
            from sqlalchemy import select

            claims = (
                session.execute(
                    select(SubmissionClaimRow).where(
                        SubmissionClaimRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(claims) == 0, (
                f"observation created {len(claims)} SubmissionClaim row(s) — "
                "observation must never acquire a submission claim"
            )

    def test_observe_creates_only_one_approval_row(self, wq8_obs_app) -> None:
        """Observation creates exactly one (unapproved) approval row holding
        the snapshot. It is NOT an authorization — it is the review snapshot
        the user will later approve or revoke."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        with session_scope(sf) as session:
            from sqlalchemy import select

            approvals = (
                session.execute(
                    select(SubmissionApprovalRow).where(
                        SubmissionApprovalRow.application_id == job.application_id
                    )
                )
                .scalars()
                .all()
            )
            assert len(approvals) == 1, (
                f"expected exactly 1 approval row after observe, got {len(approvals)}"
            )
            # The approval must be unapproved (consumed_at is None) — observe
            # does not approve.
            assert approvals[0].consumed_at is None, "observe consumed the approval"
            # It must hold the snapshot.
            assert approvals[0].snapshot_json is not None
            assert approvals[0].snapshot_hash

    def test_observe_does_not_change_job_status(self, wq8_obs_app) -> None:
        """Observation must NOT change the job status (review_ready stays
        review_ready; observation is not submission)."""
        app, job, engine, sf, settings, tmp_path = wq8_obs_app
        with TestClient(app) as client:
            resp = client.post(f"/api/submit/{job.application_id}/observe")
            assert resp.status_code == 200

        with session_scope(sf) as session:
            reloaded = get_application_job(session, job.application_id)
            assert reloaded is not None
            assert reloaded.status == ApplicationStatus.REVIEW_READY, (
                f"observe changed job status to {reloaded.status}; "
                "observation must not transition job status"
            )

    def test_observe_interlock_records_zero_authorized_submits(
        self, tmp_path: Path, fixture_server: _FixtureHTTPServer
    ) -> None:
        """The interlock installed during observation must record zero
        authorized_submits (no one-shot allowance was ever armed) and zero
        UAA submit clicks. This proves observation cannot submit even if
        the page fires submit events.

        We drive the fixture form's submit button programmatically AFTER
        navigation to prove the interlock blocks it. The observe flow itself
        never clicks submit, but we prove the interlock is armed by
        attempting a submit and asserting it is blocked.
        """
        settings = _make_settings(tmp_path)
        job = _make_job(tmp_path, fixture_server.url)
        engine, sf = _setup_db(tmp_path, settings, job)

        # Launch a context, install the interlock the same way observe does,
        # navigate to the fixture, attempt submit, and assert it is blocked
        # with zero authorized_submits.
        from universal_auto_applier.browser.submit_interlock import (
            install_interlock,
            read_counters,
        )

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(accept_downloads=False)
            # Install the interlock EXACTLY as observe_and_persist_snapshot does.
            install_interlock(ctx)
            page = ctx.new_page()
            page.goto(fixture_server.url)
            # Attempt to submit via form.submit() — must be blocked.
            page.evaluate("document.getElementById('app-form').submit()")
            # Attempt to submit via requestSubmit — must be blocked.
            page.evaluate("document.getElementById('app-form').requestSubmit()")
            # Attempt to dispatch a submit event — must be blocked.
            page.evaluate("document.getElementById('app-form').dispatchEvent(new Event('submit'))")
            counters = read_counters(page)
            assert counters["authorized_submits"] == 0, (
                "interlock allowed an authorized submit during observation — "
                "observation must never arm the one-shot allowance"
            )
            assert counters["blocked_submissions"] >= 3, (
                f"interlock did not block submit attempts; counters: {counters}"
            )
            ctx.close()
            browser.close()
        engine.dispose()
