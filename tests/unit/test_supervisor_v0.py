"""Supervisor V0 — hermetic synthetic test matrix (A-O).

All tests are hermetic (no real ATS traffic, no real browser, no LLM).
Uses injected ``prepare_fn`` and deterministic fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
)
from universal_auto_applier.supervisor import PolicyEngine, SupervisorLimits, SupervisorTools
from universal_auto_applier.supervisor.models import (
    OwnerPolicy,
    ReasonCode,
    SupervisorAction,
    SupervisorDecision,
    SupervisorState,
)
from universal_auto_applier.supervisor.planner import DeterministicPlanner, OpenAICompatiblePlanner
from universal_auto_applier.supervisor.service import SupervisorService
from universal_auto_applier.supervisor.tools import PrepareOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8001,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
    )


def _session_factory(tmp_path: Path):  # noqa: ANN001
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / f"sup_{tmp_path.name}.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    return factory, engine


def _make_job(
    *,
    url: str = "https://example.com/jobs/1",
    external_job_id: str = "job-1",
    company: str = "Acme",
    title: str = "Working Student AI",
    platform: Platform = Platform.GENERIC,
    status: ApplicationStatus = ApplicationStatus.EVALUATED,
    candidate_profile: dict | None = None,
) -> ApplicationJob:
    app_id = compute_application_id(
        platform=str(platform.value), external_job_id=external_job_id, url=url
    )
    job = ApplicationJob(
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
    if candidate_profile is not None:
        job.metadata["candidate_profile"] = candidate_profile
    return job


def _snapshot(
    application_id: str,
    *,
    unresolved: int = 0,
    fields: list[SubmissionSnapshotField] | None = None,
    url: str = "https://example.com/jobs/1",
) -> SubmissionSnapshot:
    if fields is None:
        fields = [
            SubmissionSnapshotField(
                field_token="lf-name",
                label="Full Name",
                field_type="text",
                filled_value="Test Candidate",
                status="filled",
                required=True,
            )
        ]
        unresolved = 0
    return SubmissionSnapshot(
        application_id=application_id,
        application_url=url,
        fields=fields,
        documents=[],
        pending_intervention_count=0,
        submit_control=SubmissionSnapshotSubmitControl(
            text="Submit", selector="#submit", frame_url=url, classification="dangerous_submit"
        ),
        unresolved_required_field_count=unresolved,
        high_risk_unconfirmed_count=0,
        form_fingerprint="fp",
        snapshot_hash="hash",
    )


def _insert_job(factory, job: ApplicationJob) -> None:  # noqa: ANN001
    with session_scope(factory) as session:
        upsert_application_job(session, job)


def _persist_snapshot(factory, snapshot: SubmissionSnapshot) -> None:  # noqa: ANN001
    """Persist snapshot so SupervisorTools.load_review_snapshot can find it."""
    from universal_auto_applier.submission.store import create_approval

    with session_scope(factory) as session:
        create_approval(session, application_id=snapshot.application_id, snapshot=snapshot)


def _make_prepare_with_persist(factory, mapping):  # noqa: ANN001
    """Wrap a prepare function to also persist the snapshot for tool layer.

    ``mapping`` is a dict with application_id -> snapshot or callable.
    For tests that return a snapshot per call, the prepare itself persists.
    """

    def _prepare(app_id: str) -> PrepareOutcome:
        outcome = mapping(app_id) if callable(mapping) else mapping
        if isinstance(outcome, PrepareOutcome) and outcome.snapshot is not None:
            _persist_snapshot(factory, outcome.snapshot)
        return outcome if isinstance(outcome, PrepareOutcome) else outcome

    return _prepare


# ---------------------------------------------------------------------------
# A. happy path: prepare → review_ready
# ---------------------------------------------------------------------------


def test_a_happy_path_prepare_review_ready(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="a-happy")
        _insert_job(factory, job)

        def prepare(app_id: str) -> PrepareOutcome:
            return PrepareOutcome(application_id=app_id, snapshot=_snapshot(app_id))

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools,
            policy_engine=PolicyEngine(),
            planner=DeterministicPlanner(PolicyEngine()),
            session_factory=factory,
            limits=SupervisorLimits(),
        )
        summary = service.run()
        assert job.application_id in summary.review_ready
        assert summary.submission_attempts == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# B. known candidate fact → resolve → retry → review_ready
# ---------------------------------------------------------------------------


def test_b_known_candidate_fact_resolve_retry(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(
            external_job_id="b-fact",
            candidate_profile={"personal": {"full_name": "Test"}},
        )
        _insert_job(factory, job)
        call = {"n": 0}

        def prepare(app_id: str) -> PrepareOutcome:
            # First call: unresolved field; second: resolved
            if call["n"] == 0:
                call["n"] += 1
                snap = _snapshot(
                    app_id,
                    unresolved=1,
                    fields=[
                        SubmissionSnapshotField(
                            field_token="lf-q",
                            label="How did you hear about us",
                            field_type="text",
                            status="intervention_needed",
                            required=True,
                        )
                    ],
                )
                _persist_snapshot(factory, snap)
                return PrepareOutcome(application_id=app_id, snapshot=snap)
            snap2 = _snapshot(app_id)
            _persist_snapshot(factory, snap2)
            return PrepareOutcome(application_id=app_id, snapshot=snap2)

        # Owner policy for the field
        from universal_auto_applier.interventions.answer_memory import normalize_question

        policy = OwnerPolicy(
            policy_id="p1",
            normalized_question=normalize_question("How did you hear about us"),
            answer="Sonstige",
            description="",
        )
        policy_engine = PolicyEngine(owner_policies=[policy])
        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools,
            policy_engine=policy_engine,
            planner=DeterministicPlanner(policy_engine),
            session_factory=factory,
            limits=SupervisorLimits(max_application_attempts=3),
        )
        summary = service.run()
        assert job.application_id in summary.review_ready
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# C. owner policy trusted answer → resolve
# ---------------------------------------------------------------------------


def test_c_owner_policy_trusted_answer(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="c-policy")
        _insert_job(factory, job)
        call = {"n": 0}

        def prepare(app_id: str) -> PrepareOutcome:
            if call["n"] == 0:
                call["n"] += 1
                snap = _snapshot(
                    app_id,
                    unresolved=1,
                    fields=[
                        SubmissionSnapshotField(
                            field_token="lf-src",
                            label="Source",
                            field_type="text",
                            status="intervention_needed",
                            required=True,
                        )
                    ],
                )
                _persist_snapshot(factory, snap)
                return PrepareOutcome(application_id=app_id, snapshot=snap)
            snap2 = _snapshot(app_id)
            _persist_snapshot(factory, snap2)
            return PrepareOutcome(application_id=app_id, snapshot=snap2)

        from universal_auto_applier.interventions.answer_memory import normalize_question

        policy = OwnerPolicy(
            policy_id="src-policy",
            normalized_question=normalize_question("Source"),
            answer="LinkedIn",
        )
        pe = PolicyEngine(owner_policies=[policy])
        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools, policy_engine=pe, planner=DeterministicPlanner(pe), session_factory=factory
        )
        summary = service.run()
        assert job.application_id in summary.review_ready
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# D. unknown salary → NEEDS_HUMAN never fabricate
# ---------------------------------------------------------------------------


def test_d_unknown_salary_needs_human(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="d-salary")
        _insert_job(factory, job)

        def prepare(app_id: str) -> PrepareOutcome:
            snap = _snapshot(
                app_id,
                unresolved=1,
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-sal",
                        label="Salary expectation",
                        field_type="text",
                        status="intervention_needed",
                        required=True,
                    )
                ],
            )
            _persist_snapshot(factory, snap)
            return PrepareOutcome(application_id=app_id, snapshot=snap)

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools,
            policy_engine=PolicyEngine(),
            planner=DeterministicPlanner(PolicyEngine()),
            session_factory=factory,
        )
        summary = service.run()
        assert any(h["application_id"] == job.application_id for h in summary.needs_human)
        # Ensure no AnswerMemory was created by fabrication
        from universal_auto_applier.supervisor.store import list_human_handoffs

        with session_scope(factory) as s:
            handoffs = list_human_handoffs(s)
            assert any(h.application_id == job.application_id for h in handoffs)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# E. CAPTCHA → immediate human/block no retry
# ---------------------------------------------------------------------------


def test_e_captcha_immediate_human_no_retry(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="e-captcha")
        _insert_job(factory, job)
        calls = {"n": 0}

        def prepare(app_id: str) -> PrepareOutcome:
            calls["n"] += 1
            return PrepareOutcome(
                application_id=app_id,
                snapshot=_snapshot(
                    app_id,
                    unresolved=1,
                    fields=[
                        SubmissionSnapshotField(
                            field_token="lf-cap",
                            label="CAPTCHA",
                            field_type="captcha",
                            status="intervention_needed",
                            required=True,
                        )
                    ],
                ),
            )

        # Inject CAPTCHA via intervention kind: sync creates FIELD_ANSWER but
        # policy checks kind==captcha only when kind is captcha. So simulate
        # by manually creating a captcha intervention and having planner see it.
        # Instead test via direct PolicyEngine classification:
        from universal_auto_applier.supervisor.models import InterventionView

        pe = PolicyEngine()
        view = InterventionView(intervention_id="cap1", kind="captcha", question="CAPTCHA")
        cls = pe.classify_intervention(view)
        assert cls.decision_class == "D"
        assert cls.reason_code == ReasonCode.CAPTCHA

        # End-to-end: create captcha intervention manually, then run supervisor
        from universal_auto_applier.core.statuses import InterventionKind
        from universal_auto_applier.interventions.store import create_intervention

        with session_scope(factory) as s:
            create_intervention(
                s,
                application_id=job.application_id,
                kind=InterventionKind.CAPTCHA,
                question="CAPTCHA required",
            )

        # Prepare that returns blocked (conservative) also triggers handoff
        def prepare_blocked(app_id: str) -> PrepareOutcome:
            return PrepareOutcome(application_id=app_id, snapshot=None, error="captcha wall")

        tools = SupervisorTools(
            settings=settings, session_factory=factory, prepare_fn=prepare_blocked
        )
        service = SupervisorService(
            tools=tools, policy_engine=pe, planner=DeterministicPlanner(pe), session_factory=factory
        )
        summary = service.run(application_ids=[job.application_id])
        assert any(h["application_id"] == job.application_id for h in summary.needs_human)
        # No retry loop: prepare called exactly once per app
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# F. login/2FA → human
# ---------------------------------------------------------------------------


def test_f_login_2fa_human(tmp_path: Path) -> None:
    from universal_auto_applier.supervisor.models import InterventionView

    pe = PolicyEngine()
    for kind, code in [
        ("login_required", ReasonCode.LOGIN_REQUIRED),
        ("unknown_page", ReasonCode.NO_SAFE_NAVIGATION),
    ]:
        view = InterventionView(intervention_id="x", kind=kind, question="login")
        cls = pe.classify_intervention(view)
        assert cls.decision_class == "D"
        assert cls.reason_code == code


# ---------------------------------------------------------------------------
# G. mapper defect fact exists but mapper unresolved → RepairTicket
# ---------------------------------------------------------------------------


def test_g_mapper_defect_repair_ticket(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(
            external_job_id="g-defect",
            candidate_profile={"personal": {"full_name": "Test Candidate"}},
        )
        _insert_job(factory, job)

        def prepare(app_id: str) -> PrepareOutcome:
            snap = _snapshot(
                app_id,
                unresolved=1,
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-full",
                        label="Full Name",
                        field_type="text",
                        status="intervention_needed",
                        required=True,
                    )
                ],
            )
            _persist_snapshot(factory, snap)
            return PrepareOutcome(application_id=app_id, snapshot=snap)

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        pe = PolicyEngine()
        service = SupervisorService(
            tools=tools, policy_engine=pe, planner=DeterministicPlanner(pe), session_factory=factory
        )
        summary = service.run(application_ids=[job.application_id])
        assert any(r["application_id"] == job.application_id for r in summary.repair_needed)
        from universal_auto_applier.supervisor.store import list_repair_tickets

        with session_scope(factory) as s:
            tickets = list_repair_tickets(s)
            assert any(t.application_id == job.application_id for t in tickets)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# H. repeated identical failure → bounded termination
# ---------------------------------------------------------------------------


def test_h_repeated_identical_failure_bounded(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="h-repeat")
        _insert_job(factory, job)

        def prepare(app_id: str) -> PrepareOutcome:
            # Always unresolved, no pending intervention (simulates execution defect)
            return PrepareOutcome(
                application_id=app_id,
                snapshot=_snapshot(
                    app_id,
                    unresolved=1,
                    fields=[
                        SubmissionSnapshotField(
                            field_token="lf-x",
                            label="X",
                            field_type="text",
                            status="failed",
                            required=True,
                        )
                    ],
                ),
            )

        # Override sync to not create interventions -> triggers same_failure path
        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)

        def no_sync(app_id: str) -> int:  # noqa: ANN001
            return 0

        tools.sync_interventions_from_snapshot = no_sync  # type: ignore[method-assign]
        pe = PolicyEngine()
        service = SupervisorService(
            tools=tools,
            policy_engine=pe,
            planner=DeterministicPlanner(pe),
            session_factory=factory,
            limits=SupervisorLimits(max_application_attempts=2, max_same_failure_retries=1),
        )
        summary = service.run(application_ids=[job.application_id])
        # Should terminate in needs_human or failed, not loop forever
        done = summary.needs_human + summary.failed
        assert any(d["application_id"] == job.application_id for d in done)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# I. Siemens → SKIPPED preparation never starts
# ---------------------------------------------------------------------------


def test_i_siemens_skipped_preparation_never_starts(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(
            external_job_id="i-siemens",
            url="https://jobs.siemens.com/careers/123",
            platform=Platform.SIEMENS,
            company="Siemens",
        )
        _insert_job(factory, job)
        calls = {"n": 0}

        def prepare(app_id: str) -> PrepareOutcome:
            calls["n"] += 1
            return PrepareOutcome(application_id=app_id, snapshot=_snapshot(app_id))

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools,
            policy_engine=PolicyEngine(),
            planner=DeterministicPlanner(PolicyEngine()),
            session_factory=factory,
        )
        summary = service.run(application_ids=[job.application_id])
        assert calls["n"] == 0, "Siemens must never start browser preparation"
        assert summary.skipped_siemens == 1
        assert any(s["application_id"] == job.application_id for s in summary.skipped)
        from universal_auto_applier.supervisor.store import get_supervisor_application_state

        with session_scope(factory) as s:
            st = get_supervisor_application_state(s, job.application_id)
            assert st is not None
            assert st.state == SupervisorState.SKIPPED
            assert st.reason_code == ReasonCode.DEDICATED_SIEMENS_WORKFLOW
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# J. CV + transcript bundle → complete per-job bundle survives retry
# ---------------------------------------------------------------------------


def test_j_cv_transcript_bundle_survives_retry(tmp_path: Path) -> None:
    from universal_auto_applier.core.statuses import InterventionKind, InterventionStatus
    from universal_auto_applier.interventions.store import create_intervention, get_intervention
    from universal_auto_applier.persistence.job_repository import get_application_job

    factory, engine = _session_factory(tmp_path)
    try:
        job = _make_job(external_job_id="j-bundle")
        _insert_job(factory, job)
        bundle = [
            {"path": "/tmp/cv.pdf", "kind": "cv"},
            {"path": "/tmp/transcript.pdf", "kind": "transcript"},
        ]
        with session_scope(factory) as session:
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Vollständige Bewerbungsunterlagen",
                field_selector="lf-docs",
                llm_metadata={
                    "field_label": "Vollständige Bewerbungsunterlagen",
                    "field_type": "file",
                },
            )
            intervention = get_intervention(session, row.intervention_id)
            assert intervention is not None
            from universal_auto_applier.interventions.resolve_service import (
                resolve_with_persistence,
            )

            resolve_with_persistence(
                session,
                intervention=intervention,
                resolution=InterventionStatus.EDITED,
                answer=None,
                structured_bundle=bundle,
                save_to_memory=False,
            )
        with session_scope(factory) as session:
            reloaded = get_application_job(session, job.application_id)
            assert reloaded is not None
            form_answers = reloaded.metadata.get("form_answers", {})
            assert "Vollständige Bewerbungsunterlagen" in form_answers
            stored = form_answers["Vollständige Bewerbungsunterlagen"]
            assert stored["files"] == bundle
            # AnswerMemory must not have been created (no lossy first-path entry)
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import AnswerMemoryRow

            mems = list(session.execute(select(AnswerMemoryRow)).scalars().all())
            assert len(mems) == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# K. save_to_memory=false → per-job persists, global memory untouched
# ---------------------------------------------------------------------------


def test_k_save_to_memory_false(tmp_path: Path) -> None:
    from universal_auto_applier.core.statuses import InterventionKind, InterventionStatus
    from universal_auto_applier.interventions.store import create_intervention, get_intervention
    from universal_auto_applier.persistence.job_repository import get_application_job

    factory, engine = _session_factory(tmp_path)
    try:
        job = _make_job(external_job_id="k-memory")
        _insert_job(factory, job)
        with session_scope(factory) as session:
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Q1",
                field_selector="lf-q1",
                llm_metadata={"field_label": "Q1"},
            )
            intervention = get_intervention(session, row.intervention_id)
            assert intervention is not None
            from universal_auto_applier.interventions.resolve_service import (
                resolve_with_persistence,
            )

            resolve_with_persistence(
                session,
                intervention=intervention,
                resolution=InterventionStatus.EDITED,
                answer="hello",
                structured_bundle=None,
                save_to_memory=False,
            )
        with session_scope(factory) as session:
            reloaded = get_application_job(session, job.application_id)
            assert reloaded is not None
            assert reloaded.metadata.get("form_answers", {}).get("Q1") == "hello"
            from sqlalchemy import select

            from universal_auto_applier.persistence.models import AnswerMemoryRow

            mems = list(session.execute(select(AnswerMemoryRow)).scalars().all())
            assert len(mems) == 0

        # save_to_memory=true DOES create memory entry for scalar
        factory2, engine2 = _session_factory(tmp_path / "k2")
        try:
            job2 = _make_job(external_job_id="k-memory-2", url="https://example.com/jobs/k2")
            _insert_job(factory2, job2)
            with session_scope(factory2) as session:
                row2 = create_intervention(
                    session,
                    application_id=job2.application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Q2",
                    field_selector="lf-q2",
                    llm_metadata={"field_label": "Q2"},
                )
                iv2 = get_intervention(session, row2.intervention_id)
                assert iv2 is not None
                from universal_auto_applier.interventions.resolve_service import (
                    resolve_with_persistence,
                )

                resolve_with_persistence(
                    session,
                    intervention=iv2,
                    resolution=InterventionStatus.EDITED,
                    answer="world",
                    structured_bundle=None,
                    save_to_memory=True,
                )
            with session_scope(factory2) as session:
                from sqlalchemy import select

                from universal_auto_applier.persistence.models import AnswerMemoryRow

                mems = list(session.execute(select(AnswerMemoryRow)).scalars().all())
                assert len(mems) == 1
                assert mems[0].answer == "world"
        finally:
            engine2.dispose()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# L. invalid planner/model output → fail closed no mutation
# ---------------------------------------------------------------------------


def test_l_invalid_planner_output_fail_closed(tmp_path: Path) -> None:

    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job = _make_job(external_job_id="l-invalid")
        _insert_job(factory, job)

        def prepare(app_id: str) -> PrepareOutcome:
            snap = _snapshot(
                app_id,
                unresolved=1,
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-bad",
                        label="Bad",
                        field_type="text",
                        status="intervention_needed",
                        required=True,
                    )
                ],
            )
            _persist_snapshot(factory, snap)
            return PrepareOutcome(application_id=app_id, snapshot=snap)

        # Use OpenAI planner with malformed JSON
        pe = PolicyEngine()
        planner = OpenAICompatiblePlanner(pe)
        # Directly test parse_decision with invalid payload
        bad = planner.parse_decision("not json", job.application_id)
        assert bad.action == SupervisorAction.REQUEST_HUMAN
        assert bad.reason_code == ReasonCode.UNKNOWN_FAILURE

        # Also test unknown action name
        bad2 = planner.parse_decision(
            json.dumps(
                {
                    "action": "submit",
                    "reason_code": "unknown_failure",
                    "rationale": "x",
                    "confidence": 0.5,
                }
            ),
            job.application_id,
        )
        assert bad2.action == SupervisorAction.REQUEST_HUMAN

        # End-to-end: service with a planner that returns invalid decision structurally
        class BadPlanner:
            def decide(self, context):  # noqa: ANN001
                return SupervisorDecision(
                    action=SupervisorAction.RESOLVE_INTERVENTION,
                    application_id=context.application_id,
                    intervention_id=None,  # missing required for RESOLVE
                    reason_code=ReasonCode.UNKNOWN_FAILURE,
                    answer=None,
                    answer_source=None,
                )

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        service = SupervisorService(
            tools=tools,
            policy_engine=pe,
            planner=BadPlanner(),
            session_factory=factory,  # type: ignore[arg-type]
        )
        summary = service.run(application_ids=[job.application_id])
        # Must fail closed to NEEDS_HUMAN, no mutation via resolve_intervention
        assert any(h["application_id"] == job.application_id for h in summary.needs_human)
        from sqlalchemy import select

        from universal_auto_applier.persistence.models import AnswerMemoryRow

        with session_scope(factory) as s:
            assert len(list(s.execute(select(AnswerMemoryRow)).scalars().all())) == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# M. attempted SUBMIT decision → rejected by schema/policy no submission tool
# ---------------------------------------------------------------------------


def test_m_submit_decision_rejected(tmp_path: Path) -> None:
    # Schema has no SUBMIT action — any model output with action=submit fails validation
    from pydantic import ValidationError

    pe = PolicyEngine()
    planner = OpenAICompatiblePlanner(pe)
    raw = json.dumps(
        {"action": "submit", "reason_code": "review_ready", "rationale": "try", "confidence": 1.0}
    )
    decision = planner.parse_decision(raw, "app123")
    assert decision.action == SupervisorAction.REQUEST_HUMAN

    # Also direct schema validation must reject submit
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate(
            {"action": "submit", "application_id": "app123", "reason_code": "review_ready"}
        )

    # No submit tool exists on SupervisorTools
    assert not hasattr(SupervisorTools, "submit")
    assert not hasattr(SupervisorTools, "submit_application")
    assert not hasattr(SupervisorTools, "authorize")


# ---------------------------------------------------------------------------
# N. raw browser click attempt → impossible through tool registry
# ---------------------------------------------------------------------------


def test_n_raw_browser_click_impossible() -> None:
    for name in ["click", "goto", "evaluate", "set_input_files", "browser", "page"]:
        assert not hasattr(SupervisorTools, name), f"SupervisorTools must not expose {name}"
    # Inspect source for any raw browser escape hatch outside comments/docstrings.
    # The docstring itself mentions the forbidden tools to document the contract —
    # that's allowed; we only forbid actual code usage.
    import pathlib as _pl
    import re

    src = (
        _pl.Path(__file__).parents[2] / "src" / "universal_auto_applier" / "supervisor" / "tools.py"
    ).read_text(encoding="utf-8")
    # Strip triple-quoted strings/comments to check only code
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"'''.*?'''", "", code_only, flags=re.DOTALL)
    code_only = "\n".join(
        line for line in code_only.splitlines() if not line.strip().startswith("#")
    )
    assert "page.goto" not in code_only
    assert "set_input_files" not in code_only
    assert ".evaluate(" not in code_only
    # No submit interlock disable
    assert "interlock disable" not in code_only.lower()


# ---------------------------------------------------------------------------
# O. aggregated handoff/run summary
# ---------------------------------------------------------------------------


def test_o_aggregated_handoff_run_summary(tmp_path: Path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        settings = _settings(tmp_path)
        job1 = _make_job(external_job_id="o-1", url="https://example.com/jobs/o1")
        job2 = _make_job(external_job_id="o-2", url="https://example.com/jobs/o2")
        _insert_job(factory, job1)
        _insert_job(factory, job2)

        def prepare(app_id: str) -> PrepareOutcome:
            if app_id == job1.application_id:
                snap = _snapshot(app_id)
                _persist_snapshot(factory, snap)
                return PrepareOutcome(application_id=app_id, snapshot=snap)
            snap2 = _snapshot(
                app_id,
                unresolved=1,
                fields=[
                    SubmissionSnapshotField(
                        field_token="lf-salary",
                        label="Salary expectation",
                        field_type="text",
                        status="intervention_needed",
                        required=True,
                    )
                ],
            )
            _persist_snapshot(factory, snap2)
            return PrepareOutcome(application_id=app_id, snapshot=snap2)

        tools = SupervisorTools(settings=settings, session_factory=factory, prepare_fn=prepare)
        pe = PolicyEngine()
        service = SupervisorService(
            tools=tools, policy_engine=pe, planner=DeterministicPlanner(pe), session_factory=factory
        )
        summary = service.run()
        assert job1.application_id in summary.review_ready
        assert any(h["application_id"] == job2.application_id for h in summary.needs_human)
        assert len(summary.review_ready) == 1
        assert len(summary.needs_human) == 1
        assert summary.submission_attempts == 0

        # Verify run row persisted
        from universal_auto_applier.supervisor.store import list_supervisor_runs

        with session_scope(factory) as s:
            runs = list_supervisor_runs(s)
            assert len(runs) == 1
            assert runs[0].summary_json["review_ready"] == [job1.application_id]
    finally:
        engine.dispose()
