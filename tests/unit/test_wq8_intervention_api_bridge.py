"""Hermetic test: intervention API with missing field_label falls back to question.

Drives the REAL ``POST /interventions/{id}/resolve`` endpoint (not a copy of
its logic) against a hermetic SQLite database, then disposes the engine and
reopens the database fresh to prove the bridge survives a full reload and is
consumed by the deterministic mapper with ``source=application_job``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base

URL = "https://example.com/jobs/wq8-api-bridge"
APP_ID = compute_application_id(platform="unknown", external_job_id=None, url=URL)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8391,
        data_dir=tmp_path / "uaa_data",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
    )


def _open_engine(settings: Settings) -> Any:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    engine = make_engine(db_url)
    apply_migrations(db_url)
    Base.metadata.create_all(engine)
    return engine


def _seed_job(settings: Settings) -> None:
    from universal_auto_applier.core.models import ApplicationJob

    engine = _open_engine(settings)
    factory = make_session_factory(engine)
    job = ApplicationJob(
        application_id=APP_ID,
        platform="unknown",
        source="test",
        company="C",
        title="T",
        url=URL,
        status="evaluated",
        verdict="apply",
        metadata={
            "candidate_profile": {
                "full_name": "Test Candidate",
                "first_name": "Test",
                "last_name": "Candidate",
                "email": "test.candidate@example.com",
                "phone": "+1 555 0100",
                "city": "Teststadt",
                "country": "Germany",
            }
        },
    )
    with session_scope(factory) as session:
        upsert_application_job(session, job)
    engine.dispose()


def _make_app(settings: Settings) -> Any:
    engine = _open_engine(settings)
    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.review_states = {}
    from universal_auto_applier.api.routes.logs import init_log_buffer

    init_log_buffer(app)
    return app


def test_resolve_missing_field_label_falls_back_to_question_and_survives_reload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _seed_job(settings)

    # Create an intervention whose llm_metadata has NO field_label.
    engine = _open_engine(settings)
    factory = make_session_factory(engine)
    from universal_auto_applier.core.statuses import InterventionKind
    from universal_auto_applier.interventions.store import create_intervention

    with session_scope(factory) as session:
        iv = create_intervention(
            session,
            application_id=APP_ID,
            kind=InterventionKind.FIELD_ANSWER,
            question="PLZ:*",
            field_selector="lf-test-plz",
            options=[],
            llm_metadata=None,  # missing field_label
        )
        iv_id = iv.intervention_id
    engine.dispose()

    # Resolve through the REAL API endpoint with save_to_memory.
    app = _make_app(settings)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/interventions/{iv_id}/resolve",
            json={"resolution": "edited", "answer": "91054", "save_to_memory": True},
        )
        assert resp.status_code == 200, resp.text
    app.state.engine.dispose()

    # Fresh DB reload: dispose + reopen the same SQLite file, new app instance.
    app2 = _make_app(settings)
    try:
        from universal_auto_applier.core.models import CandidateProfile, FormField
        from universal_auto_applier.form_engine.field_mapper import map_field
        from universal_auto_applier.persistence.job_repository import get_application_job

        with session_scope(app2.state.session_factory) as session:
            job = get_application_job(session, APP_ID)
            assert job is not None
            # The answer keyed by the question fallback survived the reload.
            assert job.metadata.get("form_answers", {}).get("PLZ:*") == "91054"

        candidate = CandidateProfile(
            first_name="Test",
            last_name="Candidate",
            email="test.candidate@example.com",
            city="Teststadt",
            country="Germany",
        )
        field = FormField(
            selector="lf-test-plz", name="plz", label="PLZ:*", type="text", required=True
        )
        mapping = map_field(field, candidate, job)
        assert mapping is not None
        assert mapping.value == "91054"
        assert mapping.source == "application_job"
    finally:
        app2.state.engine.dispose()
