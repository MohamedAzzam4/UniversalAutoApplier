"""Supervisor target-freshness preflight — hermetic tests (E-L).

E. valid package + live target -> preparation permitted.
F. valid package + expired target -> APPLICATION_EXPIRED, prepare calls 0.
G. Workday expired evidence (Pilot-01 URL shape + not-found page markers).
H. expired target -> no HUMAN_REQUIRED artifacts (no handoff, no intervention).
I. supervisor-application --json exposes sanitized failure evidence.
J. queue [expired A, live B] -> A skipped, B continues (concurrency 1).
K. document lineage gate still enforced and unchanged.
L. submission_attempts == 0 in every review-only test here.

Hermetic: freshness outcomes are injected; the only live-network module
(target_preflight) is unit-tested with stubbed httpx. No browser, no LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from sqlalchemy import event

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
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
)
from universal_auto_applier.supervisor import PolicyEngine, SupervisorLimits, SupervisorTools
from universal_auto_applier.supervisor.models import ReasonCode
from universal_auto_applier.supervisor.planner import DeterministicPlanner
from universal_auto_applier.supervisor.service import SupervisorService
from universal_auto_applier.supervisor.target_preflight import (
    EXPIRED,
    LIVE,
    UNKNOWN,
    WORKDAY_NOT_FOUND_MARKERS,
    check_smartrecruiters,
    check_target_freshness,
    check_workday,
    parse_workday_identity,
)
from universal_auto_applier.supervisor.tools import PrepareOutcome

PILOT_URL = (
    "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/"
    "Nuremberg/Werkstudent-Data-Analytics---Business-Analytics--m-w-d-_ID15339"
)


# ---------------------------------------------------------------------------
# Helpers (mirror the supervisor v0 harness, sharing the CLI's store)
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
    )


def _session_factory(tmp_path: Path):
    """Session factory on the SAME store the CLI opens (uaa.sqlite)."""
    data_dir = tmp_path / "uaa_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    database_url = build_engine_url(data_dir / "uaa.sqlite")
    apply_migrations(database_url)
    engine = make_engine(database_url)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return make_session_factory(engine), engine


def _make_job(
    *,
    url: str = "https://example.com/jobs/1",
    external_job_id: str = "job-1",
    company: str = "Acme",
    title: str = "Working Student AI",
    platform: Platform = Platform.GENERIC,
    status: ApplicationStatus = ApplicationStatus.EVALUATED,
) -> ApplicationJob:
    app_id = compute_application_id(
        platform=str(platform.value), external_job_id=external_job_id, url=url
    )
    return ApplicationJob(
        application_id=app_id,
        platform=platform,
        source="test",
        company=company,
        title=title,
        url=url,
        location="Munich, Germany",
        verdict="apply",
        status=status,
        external_job_id=external_job_id,
    )


def _snapshot(application_id: str) -> SubmissionSnapshot:
    return SubmissionSnapshot(
        application_id=application_id,
        application_url="https://example.com/jobs/1",
        fields=[
            SubmissionSnapshotField(
                field_token="lf-name",
                label="Full Name",
                field_type="text",
                filled_value="Test Candidate",
                status="filled",
                required=True,
            )
        ],
        documents=[],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit",
            selector="#submit",
            frame_url="https://example.com/jobs/1",
            classification="dangerous_submit",
        ),
        final_boundary_confirmed=True,
        completed_form_step_count=1,
        form_progress_fingerprint="supervisor-freshness-test-progress",
        unresolved_required_field_count=0,
        high_risk_unconfirmed_count=0,
        form_fingerprint="fp",
        snapshot_hash="hash",
    )


def _insert_job(factory, job: ApplicationJob) -> None:
    with session_scope(factory) as session:
        upsert_application_job(session, job)


def _service(factory, settings, prepare_calls, freshness, snapshots=True):
    def prepare(app_id: str) -> PrepareOutcome:
        prepare_calls.append(app_id)
        return PrepareOutcome(application_id=app_id, snapshot=_snapshot(app_id))

    tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
    policy = PolicyEngine()
    return SupervisorService(
        tools=tools,
        policy_engine=policy,
        planner=DeterministicPlanner(policy),
        session_factory=factory,
        limits=SupervisorLimits(),
        freshness_fn=lambda url, platform: freshness,
    )


# ---------------------------------------------------------------------------
# E/F: live target prepares, expired target skips with zero prepare calls
# ---------------------------------------------------------------------------


class TestPreflightGate:
    def test_e_live_target_prepares(self, tmp_path: Path) -> None:
        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job = _make_job(external_job_id="e-live")
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (LIVE, "present"))
            summary = service.run()
            assert prepare_calls, "prepare must be called for a live target"
            assert job.application_id in summary.review_ready
            assert summary.submission_attempts == 0
        finally:
            engine.dispose()

    def test_f_expired_target_skips_without_prepare(self, tmp_path: Path) -> None:
        from universal_auto_applier.supervisor.store import list_human_handoffs

        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job = _make_job(external_job_id="f-expired")
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (EXPIRED, "posting gone"))
            summary = service.run()
            assert prepare_calls == [], "prepare must never run for expired target"
            assert job.application_id not in summary.review_ready
            skipped = [s for s in summary.skipped if s["application_id"] == job.application_id]
            assert len(skipped) == 1
            assert skipped[0]["reason_code"] == ReasonCode.APPLICATION_EXPIRED.value
            with session_scope(factory) as session:
                assert list_human_handoffs(session) == []
            assert summary.submission_attempts == 0
        finally:
            engine.dispose()

    def test_unknown_target_continues(self, tmp_path: Path) -> None:
        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job = _make_job(external_job_id="e-unknown")
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (UNKNOWN, "no lookup"))
            summary = service.run()
            assert prepare_calls, "UNKNOWN must not block preparation"
            assert job.application_id in summary.review_ready
            assert summary.submission_attempts == 0
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# G: Pilot-01 expired evidence
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _fake_client(handler):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            return handler("POST", url, json or {})

        def get(self, url, headers=None):
            return handler("GET", url, {})

    return FakeClient


class TestPilotEvidence:
    def test_g_pilot_url_shape_parses(self):
        tenant, board, req_id = parse_workday_identity(PILOT_URL)
        assert tenant == "datev"
        assert board == "Datev_Careers"
        assert req_id == "ID15339"

    def test_g_pilot_url_absent_from_index_is_expired(self, monkeypatch):
        monkeypatch.setattr(
            httpx,
            "Client",
            _fake_client(lambda m, u, p: FakeResp(200, {"total": 0, "jobPostings": []})),
        )
        status, detail = check_workday(PILOT_URL)
        assert status == EXPIRED
        assert "ID15339" in detail

    def test_g_not_found_page_markers_recognized(self, monkeypatch):
        shell = "<html><body><div id=root></div></body></html>"
        marker_page = (
            "<html><body><h1>Die Seite, die Sie suchen, ist nicht vorhanden.</h1></body></html>"
        )
        assert any(m in marker_page.lower() for m in WORKDAY_NOT_FOUND_MARKERS)

        def handler(method, url, payload):
            return FakeResp(200, text=shell + marker_page)

        # URL without a requisition token falls back to page-text evidence.
        monkeypatch.setattr(httpx, "Client", _fake_client(handler))
        status, _ = check_workday("https://acme.wd3.myworkdayjobs.com/en-US/acme/job/1")
        assert status == EXPIRED

    def test_g_live_index_entry_is_live(self, monkeypatch):
        def handler(method, url, payload):
            return FakeResp(
                200,
                {
                    "total": 1,
                    "jobPostings": [{"externalPath": "/job/Nuremberg/X_ID15374"}],
                },
            )

        monkeypatch.setattr(httpx, "Client", _fake_client(handler))
        status, _ = check_workday(
            "https://datev.wd3.myworkdayjobs.com/de-DE/Datev_Careers/job/Nuremberg/X_ID15374"
        )
        assert status == LIVE


class TestSmartRecruitersPreflight:
    def test_200_is_live(self, monkeypatch):
        monkeypatch.setattr(
            httpx, "Client", _fake_client(lambda m, u, p: FakeResp(200, {"id": "1"}))
        )
        status, _ = check_smartrecruiters(
            "https://jobs.smartrecruiters.com/BoschGroup/744000147175004"
        )
        assert status == LIVE

    def test_404_is_expired(self, monkeypatch):
        monkeypatch.setattr(httpx, "Client", _fake_client(lambda m, u, p: FakeResp(404)))
        status, _ = check_smartrecruiters(
            "https://jobs.smartrecruiters.com/BoschGroup/000000000000000"
        )
        assert status == EXPIRED

    def test_unknown_host_is_unknown(self):
        status, _ = check_target_freshness("https://example.com/jobs/1")
        assert status == UNKNOWN


# ---------------------------------------------------------------------------
# H: no human-required artifacts for expired targets
# ---------------------------------------------------------------------------


class TestNoHandoffForExpired:
    def test_h_no_handoff_no_intervention(self, tmp_path: Path) -> None:
        from universal_auto_applier.interventions.store import list_pending_interventions
        from universal_auto_applier.supervisor.store import list_human_handoffs

        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job = _make_job(external_job_id="h-expired")
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (EXPIRED, "posting gone"))
            service.run()
            with session_scope(factory) as session:
                assert list_human_handoffs(session) == []
                assert list_pending_interventions(session, job.application_id) == []
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# I: CLI exposes sanitized failure evidence in one shot
# ---------------------------------------------------------------------------


class TestCliFailureEvidence:
    def test_i_application_json_has_evidence(self, tmp_path: Path, capsys) -> None:
        from universal_auto_applier.cli import run_command

        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job = _make_job(external_job_id="i-expired")
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (EXPIRED, "posting gone"))
            service.run()

            code = run_command(
                [
                    "supervisor-application",
                    "--application-id",
                    job.application_id,
                    "--json",
                ],
                settings,
            )
            assert code == 0
            out = capsys.readouterr().out
            payload = json.loads(out)
            evidence = payload.get("failure_evidence")
            assert evidence is not None
            assert evidence["reason_code"] == ReasonCode.APPLICATION_EXPIRED.value
            assert evidence["failure_stage"] == "target-preflight"
            assert evidence["target_status"] == "expired"
            assert evidence["ats"] == "generic"
            assert evidence["interaction_ready"] is False
            assert prepare_calls == []
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# J: batch [expired A, live B]
# ---------------------------------------------------------------------------


class TestBatchExpiredAndLive:
    def test_j_expired_skipped_live_continues(self, tmp_path: Path) -> None:
        from universal_auto_applier.supervisor.store import list_human_handoffs
        from universal_auto_applier.supervisor.tools import SupervisorTools

        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            job_a = _make_job(external_job_id="j-expired", company="ExpiredCo")
            job_b = _make_job(external_job_id="j-live", company="LiveCo")
            _insert_job(factory, job_a)
            _insert_job(factory, job_b)
            calls: dict[str, int] = {}

            def routed_prepare(app_id: str) -> PrepareOutcome:
                calls[app_id] = calls.get(app_id, 0) + 1
                return PrepareOutcome(application_id=app_id, snapshot=_snapshot(app_id))

            tools = SupervisorTools(
                settings=settings, session_factory=factory, prepare_fn=routed_prepare
            )
            policy = PolicyEngine()
            expired_ids = {job_a.application_id}
            service = SupervisorService(
                tools=tools,
                policy_engine=policy,
                planner=DeterministicPlanner(policy),
                session_factory=factory,
                limits=SupervisorLimits(),
                freshness_fn=lambda url, platform: (LIVE, "present"),
            )

            # Expire A by application identity (both share example.com host).
            def check_job(job):
                if job.application_id in expired_ids:
                    return EXPIRED, "posting gone"
                return LIVE, "present"

            service._check_target_freshness = check_job
            summary = service.run()

            assert calls.get(job_a.application_id, 0) == 0
            assert calls.get(job_b.application_id, 0) >= 1
            skipped_ids = [s["application_id"] for s in summary.skipped]
            assert job_a.application_id in skipped_ids
            assert job_b.application_id in summary.review_ready
            with session_scope(factory) as session:
                assert list_human_handoffs(session) == []
            assert summary.submission_attempts == 0
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# K: lineage gate unchanged
# ---------------------------------------------------------------------------


class TestLineageUnchanged:
    def test_k_lineage_mismatch_still_blocks(self, tmp_path: Path) -> None:
        import hashlib

        from universal_auto_applier.supervisor.store import list_human_handoffs

        factory, engine = _session_factory(tmp_path)
        try:
            settings = _settings(tmp_path)
            file_a = tmp_path / "cv_a.pdf"
            file_b = tmp_path / "cv_b.pdf"
            file_a.write_bytes(b"cv A")
            file_b.write_bytes(b"cv B")
            stored = hashlib.sha256(b"cv A").hexdigest()[:32]
            job = _make_job(external_job_id="k-lineage")
            job.cv_pdf = str(file_b)
            job.metadata["document_hashes"] = {"cv_pdf": stored}
            _insert_job(factory, job)
            prepare_calls: list[str] = []
            service = _service(factory, settings, prepare_calls, (LIVE, "present"))
            summary = service.run()
            assert prepare_calls == []
            with session_scope(factory) as session:
                handoffs = list_human_handoffs(session)
            assert len(handoffs) == 1
            assert summary.submission_attempts == 0
        finally:
            engine.dispose()
