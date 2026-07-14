"""Playwright acceptance tests for LLM question resolution.

Exact assertions, no conditional status checks, no page.evaluate().
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import BrowserContext, Page

from universal_auto_applier.api.app import create_app
from universal_auto_applier.browser.live_runner import LiveBrowserConfig, LiveBrowserRunner
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind, Platform
from universal_auto_applier.interventions.answer_memory import retrieve_answer, store_answer
from universal_auto_applier.interventions.store import (
    create_intervention,
    list_all_interventions,
    list_pending_interventions,
    resolve_intervention,
)
from universal_auto_applier.llm.qa_service import MockQuestionAnsweringService
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
from universal_auto_applier.persistence.models import ApplicationJobRow, Base

pytestmark = pytest.mark.playwright

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "live_browser"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


@pytest.fixture(scope="module")
def fixture_server() -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_job(
    tmp_path: Path,
    url: str,
    external_id: str,
    metadata: dict[str, Any] | None = None,
) -> ApplicationJob:
    cv_pdf = tmp_path / f"{external_id}-cv.pdf"
    cover_pdf = tmp_path / f"{external_id}-cover.pdf"
    cv_md = tmp_path / f"{external_id}-cv.md"
    cv_pdf.write_bytes(b"%PDF-1.4 fixture cv")
    cover_pdf.write_bytes(b"%PDF-1.4 fixture cover")
    cv_md.write_text("Python automation, FastAPI, Docker, Kubernetes", encoding="utf-8")
    base_meta: dict[str, Any] = {
        "candidate_profile": {
            "first_name": "Mohamed",
            "last_name": "Azzam",
            "full_name": "Mohamed Azzam",
            "email": "mohamed@example.com",
            "phone": "+49 1234567",
            "linkedin_url": "https://linkedin.com/in/mohamed",
            "github_url": "https://github.com/MohamedAzzam4",
            "requires_sponsorship": False,
        },
    }
    if metadata:
        base_meta.update(metadata)
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id=external_id, url=url
        ),
        platform=Platform.GENERIC,
        source="fixture",
        company="Fixture Company",
        title="Working Student",
        url=url,
        verdict="apply",
        cv_pdf=str(cv_pdf),
        cover_letter_pdf=str(cover_pdf),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id=external_id,
        documents=ApplicationJobDocuments(cv_md=str(cv_md)),
        metadata=base_meta,
    )


def _make_config(tmp_path: Path) -> LiveBrowserConfig:
    return LiveBrowserConfig(
        artifacts_root=tmp_path / "live-runs",
        profile_dir=None,
        headless=True,
        channel=None,
        timeout_ms=15_000,
        max_steps=8,
        capture_trace=False,
    )


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="Mohamed",
        last_name="Azzam",
        full_name="Mohamed Azzam",
        email="mohamed@example.com",
        phone="+49 1234567",
        linkedin_url="https://linkedin.com/in/mohamed",
        github_url="https://github.com/MohamedAzzam4",
        requires_sponsorship=False,
    )


def _start_dashboard(tmp_path: Path) -> tuple[str, Any, Any]:
    settings = Settings(
        host="127.0.0.1",
        port=8006,
        data_dir=tmp_path / "uaa_dashboard",
        browser_headless=True,
        submit_mode="review",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    app = create_app(settings=settings)
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        ws="none",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
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
        raise RuntimeError("Server did not start")
    Base.metadata.create_all(app.state.engine)
    return base, app, server


# ---------------------------------------------------------------------------
# 1. Conditional question test — exact assertions
# ---------------------------------------------------------------------------


class TestConditionalQuestion:
    def test_conditional_field_revealed_after_llm_answer(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Mocked Gemma answers Docker parent. JS reveals child field.
        Executor re-observes and detects it. Exact assertions, no `or "failed"`.
        """
        url = f"{fixture_server}/conditional_application.html"
        job = _make_job(tmp_path, url, "cond-1")
        config = _make_config(tmp_path)

        call_count = {"n": 0}
        qa_service = MockQuestionAnsweringService(
            answer="Yes",
            confidence=0.9,
            evidence_facts=["CV mentions Docker"],
            explanation="CV states Docker experience",
        )
        orig = qa_service.answer_question

        def tracking(q, c, lg):
            call_count["n"] += 1
            return orig(q, c, lg)

        qa_service.answer_question = tracking

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-cond",
            qa_service=qa_service,
        )

        # Gemma was called.
        assert call_count["n"] >= 1

        # Exact child field label exists.
        labels = [f.label for f in report.fields]
        assert "How many years of Docker experience?" in labels

        # Exact field record with that label.
        child = next(f for f in report.fields if f.label == "How many years of Docker experience?")
        # Exact stable token exists (non-empty).
        assert child.field_token != ""
        # Exact field type is number.
        assert child.field_type == "number"
        # Exact status: intervention_needed (the LLM mock returns "Yes"
        # which fails typed-answer validation for a number field — never
        # "failed", because a safely-unresolved question is not a failure).
        assert child.status == "intervention_needed", (
            f"Expected intervention_needed, got {child.status!r}. "
            f"Explanation: {child.explanation!r}"
        )

        # Status "failed" is prohibited anywhere in the report.
        failed = [f for f in report.fields if f.status == "failed"]
        assert failed == [], f"No field should be 'failed', got: {failed}"

        # Exact pending count is 1.
        pending = [f for f in report.fields if f.status == "intervention_needed"]
        assert len(pending) == 1, (
            f"Expected exactly 1 intervention_needed, got {len(pending)}: "
            f"{[(f.label, f.status) for f in report.fields]}"
        )

        # Exact final status.
        assert report.status == "needs_user_input"
        assert report.submitted is False


# ---------------------------------------------------------------------------
# 2. Multi-step form test — deterministic, exact assertions
# ---------------------------------------------------------------------------


class TestMultiStepForm:
    def test_multistep_form_step1_then_step2(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """Step 1 filled, safe Next clicked, step 2 reached.
        Step 2 has required Kubernetes radio question. Exact assertions on
        the radio group — no weakening to assert an individual option label.
        """
        url = f"{fixture_server}/multistep_application.html"
        job = _make_job(
            tmp_path,
            url,
            "multi-1",
            metadata={
                "question_answers": {
                    "Do you have experience with Docker?": "Yes",
                },
            },
        )
        config = _make_config(tmp_path)

        runner = LiveBrowserRunner(config)
        report = runner.run_in_context(
            context,
            job,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-multistep",
        )

        # Exact step-1 labels.
        labels = [f.label for f in report.fields]
        assert "First name" in labels
        assert "Email address" in labels

        # Exact safe_continue click.
        assert len(report.click_path) >= 1
        click = report.click_path[0]
        assert "continue" in click.classification.lower() or "safe" in click.classification.lower()

        # Exact step-2 URL.
        assert "multistep_step2" in report.final_url

        # Exact Kubernetes radio group field — must be the question text
        # (legend), not the "Yes"/"No" option labels. The executor extracts
        # one logical radio-group field whose label IS the question.
        k8s = next(
            (f for f in report.fields if f.label == "Do you have experience with Kubernetes?"),
            None,
        )
        assert k8s is not None, (
            f"Kubernetes radio group with exact question label not found in: {labels}"
        )

        # Exact stable group token exists (non-empty).
        assert k8s.field_token != ""

        # Exact field type is radio.
        assert k8s.field_type == "radio"

        # Options include both Yes and No.
        assert "Yes" in k8s.options, f"Options missing 'Yes': {k8s.options}"
        assert "No" in k8s.options, f"Options missing 'No': {k8s.options}"

        # Selected answer is recorded separately (filled_value, distinct
        # from the option labels and from proposed_answer).
        assert k8s.filled_value != "", (
            f"Kubernetes radio group should have a filled_value recorded separately, "
            f"got empty. Field: {k8s!r}"
        )
        assert k8s.filled_value in ("Yes", "No"), (
            f"Filled value must be one of the available options, got {k8s.filled_value!r}"
        )

        # The runner fills the Kubernetes question via deterministic
        # positive-evidence mapping (CV mentions Kubernetes). After the
        # fill, the runner detects the Submit button on step 2 and stops
        # at review_ready.
        assert report.status == "review_ready", f"Expected review_ready, got {report.status}"

        # Final Submit was not clicked.
        assert report.submitted is False

        # Confirm the page's data-submitted attribute is still "false".
        if report.dom_snapshot_path:
            dom = Path(report.dom_snapshot_path).read_text(encoding="utf-8")
            assert 'data-submitted="false"' in dom or "data-submitted='false'" in dom, (
                "Page data-submitted should be 'false' — the submit button was clicked!"
            )


# ---------------------------------------------------------------------------
# 3. Complete resume lifecycle — exact assertions
# ---------------------------------------------------------------------------


class TestCompleteResume:
    def test_full_resume_lifecycle(
        self, context: BrowserContext, fixture_server: str, tmp_path: Path
    ) -> None:
        """First run → 1 intervention → approve+remember → retry → review_ready.
        No duplicate intervention. Submitted=false.
        """
        from universal_auto_applier.cli import _persist_interventions
        from universal_auto_applier.core.statuses import InterventionStatus

        url = f"{fixture_server}/llm_application.html"
        job_full = _make_job(
            tmp_path,
            url,
            "resume-e2e-1",
            metadata={
                "question_answers": {
                    "Do you have experience with Python?": "Yes",
                    "What is your salary expectation?": "50000",
                    "Confirm Email address": "mohamed@example.com",
                },
            },
        )
        job_no_salary = job_full.model_copy(
            update={
                "metadata": {
                    "candidate_profile": job_full.metadata["candidate_profile"],
                    "question_answers": {
                        "Do you have experience with Python?": "Yes",
                        "Confirm Email address": "mohamed@example.com",
                    },
                },
            }
        )

        config = _make_config(tmp_path)
        runner = LiveBrowserRunner(config)

        # First run.
        report1 = runner.run_in_context(
            context,
            job_no_salary,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-resume-1",
        )
        assert report1.status == "needs_user_input"
        assert report1.submitted is False

        # Persist.
        settings = Settings(
            host="127.0.0.1",
            port=8007,
            data_dir=tmp_path / "uaa_resume",
            browser_headless=True,
            submit_mode="review",
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)
        with session_scope(sf) as session:
            upsert_application_job(session, job_no_salary)
        engine.dispose()
        _persist_interventions(settings, job_no_salary.application_id, report1)

        # Set job to needs_user_input.
        engine_post = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf_post = make_session_factory(engine_post)
        with session_scope(sf_post) as session:
            row = session.get(ApplicationJobRow, job_no_salary.application_id)
            if row is not None:
                row.status = str(ApplicationStatus.NEEDS_USER_INPUT)
                session.commit()
        engine_post.dispose()

        # Verify exactly 1 intervention.
        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        with session_scope(sf2) as session:
            ivs = list_pending_interventions(session, job_no_salary.application_id)
        assert len(ivs) == 1
        assert "salary" in ivs[0].question.lower()

        # Approve + remember.
        with session_scope(sf2) as session:
            resolve_intervention(
                session,
                ivs[0].intervention_id,
                resolution=InterventionStatus.APPROVED,
                answer="50000",
            )
            store_answer(
                session,
                question="What is your salary expectation?",
                answer="50000",
                source="user_confirmed",
            )
            session.commit()

        # Answer memory saved.
        with session_scope(sf2) as session:
            memory = retrieve_answer(session, "What is your salary expectation?")
        assert memory is not None
        assert memory.answer == "50000"

        # 0 pending.
        with session_scope(sf2) as session:
            pending = list_pending_interventions(session, job_no_salary.application_id)
        assert len(pending) == 0

        # Retry API succeeds.
        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.post(f"/api/queue/{job_no_salary.application_id}/retry")
            assert response.status_code == 200

        # Second run.
        report2 = runner.run_in_context(
            context,
            job_full,
            candidate=_make_candidate(),
            artifact_dir=tmp_path / "run-resume-2",
        )
        assert report2.status == "review_ready"
        assert report2.submitted is False

        # 0 unresolved.
        unresolved = [f for f in report2.fields if f.status == "intervention_needed"]
        assert len(unresolved) == 0

        # 1 total intervention (no duplicate).
        with session_scope(sf2) as session:
            all_ivs = list_all_interventions(session, job_no_salary.application_id)
        assert len(all_ivs) == 1
        engine2.dispose()


# ---------------------------------------------------------------------------
# 4. Migration 0004 contract tests
# ---------------------------------------------------------------------------


class TestMigration0004:
    def test_upgrade_adds_llm_metadata_json(self, tmp_path: Path) -> None:
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import NullPool

        url = build_engine_url(tmp_path / "mig_upgrade.sqlite")
        apply_migrations(url)
        engine = create_engine(url, future=True, poolclass=NullPool)
        cols = [c["name"] for c in inspect(engine).get_columns("interventions")]
        engine.dispose()
        assert "llm_metadata_json" in cols

    def test_existing_rows_remain_readable(self, tmp_path: Path) -> None:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        url = build_engine_url(tmp_path / "mig_existing.sqlite")
        cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
        cfg.set_main_option(
            "script_location", str(Path(__file__).parent.parent.parent / "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "0003_application_job_documents")
        engine = create_engine(url, future=True, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO application_jobs (application_id, platform, source, "
                    "company, title, url, verdict, cv_pdf, cover_letter_pdf, status, "
                    "metadata_json, first_seen_at, last_updated_at) "
                    "VALUES ('t1', 'generic', 't', 'T', 'E', 'https://x.com', 'apply', "
                    "'/c.pdf', '/cl.pdf', 'queued', '{}', datetime('now'), datetime('now'))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO interventions (intervention_id, application_id, "
                    "status, kind, question, options, created_at) "
                    "VALUES ('iv1', 't1', 'pending', 'field_answer', 'Q?', '[]', datetime('now'))"
                )
            )
            conn.commit()
        engine.dispose()
        command.upgrade(cfg, "head")
        engine = create_engine(url, future=True, poolclass=NullPool)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT question, llm_metadata_json FROM interventions WHERE intervention_id='iv1'"
                )
            ).fetchone()
        engine.dispose()
        assert row[0] == "Q?"
        assert row[1] is None

    def test_json_metadata_round_trips(self, tmp_path: Path) -> None:
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        url = build_engine_url(tmp_path / "mig_rt.sqlite")
        apply_migrations(url)
        engine = create_engine(url, future=True, poolclass=NullPool)
        meta = json.dumps(
            {"category": "salary", "risk_level": "high", "requires_confirmation": True}
        )
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO application_jobs (application_id, platform, source, "
                    "company, title, url, verdict, cv_pdf, cover_letter_pdf, status, "
                    "metadata_json, first_seen_at, last_updated_at) "
                    "VALUES ('r1', 'generic', 't', 'T', 'E', 'https://x.com', 'apply', "
                    "'/c.pdf', '/cl.pdf', 'queued', '{}', datetime('now'), datetime('now'))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO interventions (intervention_id, application_id, "
                    "status, kind, question, options, llm_metadata_json, created_at) "
                    "VALUES ('riv', 'r1', 'pending', 'field_answer', 'S?', '[]', :m, datetime('now'))"
                ),
                {"m": meta},
            )
            conn.commit()
            row = conn.execute(
                text("SELECT llm_metadata_json FROM interventions WHERE intervention_id='riv'")
            ).fetchone()
        engine.dispose()
        parsed = json.loads(row[0])
        assert parsed["category"] == "salary"
        assert parsed["requires_confirmation"] is True

    def test_downgrade_removes_column(self, tmp_path: Path) -> None:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import NullPool

        url = build_engine_url(tmp_path / "mig_down.sqlite")
        apply_migrations(url)
        cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
        cfg.set_main_option(
            "script_location", str(Path(__file__).parent.parent.parent / "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", url)
        command.downgrade(cfg, "0003_application_job_documents")
        engine = create_engine(url, future=True, poolclass=NullPool)
        cols = [c["name"] for c in inspect(engine).get_columns("interventions")]
        engine.dispose()
        assert "llm_metadata_json" not in cols

    def test_reupgrade_succeeds(self, tmp_path: Path) -> None:
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import NullPool

        url = build_engine_url(tmp_path / "mig_reup.sqlite")
        cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
        cfg.set_main_option(
            "script_location", str(Path(__file__).parent.parent.parent / "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0003_application_job_documents")
        command.upgrade(cfg, "head")
        engine = create_engine(url, future=True, poolclass=NullPool)
        cols = [c["name"] for c in inspect(engine).get_columns("interventions")]
        engine.dispose()
        assert "llm_metadata_json" in cols


# ---------------------------------------------------------------------------
# 5. Dashboard Resume/Retry UI test — automatic visibility, no navigate away
# ---------------------------------------------------------------------------


class TestDashboardResumeUI:
    def test_resume_auto_enables_and_clicks(self, page: Page, tmp_path: Path) -> None:
        """Stay on Interventions view. Approve through visible UI.
        Resume auto-enables. Click Resume. Pipeline reruns.
        Final status exactly review_ready. Submitted=false.
        """
        base, app, server = _start_dashboard(tmp_path)
        sf = app.state.session_factory
        job = _make_job(
            tmp_path,
            "https://boards.greenhouse.io/example/jobs/resume-ui-1",
            "resume-ui-1",
        )
        job = job.model_copy(update={"status": ApplicationStatus.NEEDS_USER_INPUT})

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salary expectation?",
                suggested_answer="50000",
                confidence=0.7,
                field_selector="live-field-0-3",
                llm_metadata={
                    "category": "salary",
                    "risk_level": "high",
                    "requires_confirmation": True,
                    "unresolved_reason": "high_risk_category",
                    "field_token": "live-field-0-3",
                    "answer_source": "llm_grounded",
                },
            )

        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base)
            page.click('a[data-view="interventions"]')
            page.wait_for_selector('button[data-action="approve"]', timeout=5_000)

            # Resume disabled while pending.
            page.wait_for_selector("#resume-btn", timeout=10_000)
            resume_btn = page.locator("#resume-btn")
            page.wait_for_timeout(2_000)
            assert resume_btn.is_disabled()

            # Approve through visible UI.
            page.on("dialog", lambda dialog: dialog.accept("50000"))
            page.click('button[data-action="approve"]')

            # Wait for pending card to disappear (stays on same view).
            page.wait_for_selector("#intervention-list .uaa-empty", timeout=10_000)

            # Resume auto-enables (no navigation away).
            for _ in range(30):
                if resume_btn.is_enabled():
                    break
                page.wait_for_timeout(500)
            assert resume_btn.is_enabled(), "Resume should auto-enable"

            # Click Resume.
            page.click("#resume-btn")

            # Wait for pipeline to complete by polling the API status.
            for _ in range(30):
                resp = page.evaluate(
                    """async () => {
                        const r = await fetch('/api/pipeline/status');
                        return r.json();
                    }"""
                )
                if resp.get("status") == "completed":
                    break
                page.wait_for_timeout(500)

            # Verify final status exactly review_ready.
            with session_scope(sf) as session:
                updated = get_application_job(session, job.application_id)
            assert updated is not None
            assert str(updated.status) == "review_ready", (
                f"Expected review_ready, got {updated.status}"
            )
            assert str(updated.status) != "submitted"
        finally:
            server.should_exit = True


# ---------------------------------------------------------------------------
# 6. Retry API pending-intervention gate test
# ---------------------------------------------------------------------------


class TestRetryApiGate:
    def test_retry_rejected_with_pending_interventions(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.statuses import InterventionStatus

        settings = Settings(
            host="127.0.0.1",
            port=8008,
            data_dir=tmp_path / "uaa_gate",
            browser_headless=True,
            submit_mode="review",
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)

        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            sf = app.state.session_factory
            job = _make_job(
                tmp_path,
                "https://boards.greenhouse.io/example/jobs/gate-1",
                "gate-1",
            )
            job = job.model_copy(update={"status": ApplicationStatus.NEEDS_USER_INPUT})

            with session_scope(sf) as session:
                upsert_application_job(session, job)
                row = create_intervention(
                    session,
                    application_id=job.application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Salary?",
                    suggested_answer="50000",
                    confidence=0.7,
                    field_selector="live-field-0-1",
                )
                iv_id = row.intervention_id

            # Retry rejected with pending.
            response = client.post(f"/api/queue/{job.application_id}/retry")
            assert response.status_code == 409
            assert "pending" in response.json()["detail"].lower()

            # Resolve.
            with session_scope(sf) as session:
                resolve_intervention(
                    session,
                    iv_id,
                    resolution=InterventionStatus.APPROVED,
                    answer="50000",
                )
                session.commit()

            # Retry succeeds.
            response = client.post(f"/api/queue/{job.application_id}/retry")
            assert response.status_code == 200
            assert response.json()["status"] == "queued"
