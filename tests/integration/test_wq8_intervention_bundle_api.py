"""WQ-8 intervention bundle API — official edit path persists structured bundle."""

from pathlib import Path

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, FormField
from universal_auto_applier.core.statuses import ApplicationStatus, InterventionKind, Platform
from universal_auto_applier.form_engine.field_mapper import map_field
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


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8320,
        data_dir=tmp_path / "uaa_bundle_api",
        browser_headless=True,
        submit_mode="review",
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    url = "https://example.test/jobs/bundle-api"
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="bundle-api", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Example",
        title="Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.REVIEW_READY,
        external_job_id="bundle-api",
        metadata={},
    )


def test_intervention_api_persists_structured_bundle(tmp_path: Path) -> None:
    """Official API/edit path → persist bundle → fresh DB reload → mapper consumes."""
    settings = _make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
    sf = make_session_factory(engine)
    job = _make_job(tmp_path)

    with session_scope(sf) as s:
        upsert_application_job(s, job)

    # Create a generic file field intervention (Vollständige Bewerbungsunterlagen)
    from universal_auto_applier.interventions.store import create_intervention

    field_label = "Vollständige Bewerbungsunterlagen"
    field_selector = "lf-bundle-test"
    with session_scope(sf) as s:
        create_intervention(
            s,
            application_id=job.application_id,
            kind=InterventionKind.FIELD_ANSWER,
            question=field_label,
            field_selector=field_selector,
            llm_metadata={"field_label": field_label},
        )

    # Create synthetic documents
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"cv for api test")
    tr = tmp_path / "transcript.pdf"
    tr.write_bytes(b"transcript for api test")

    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = sf

    # Resolve via official API with structured file_bundle
    with TestClient(app) as client:
        # Find intervention
        resp = client.get(f"/api/interventions?application_id={job.application_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        iid = data["interventions"][0]["intervention_id"]

        # Resolve with file_bundle (structured)
        resp2 = client.post(
            f"/api/interventions/{iid}/resolve",
            json={
                "resolution": "edited",
                "file_bundle": [
                    {"path": str(cv), "kind": "cv"},
                    {"path": str(tr), "kind": "transcript"},
                ],
                "save_to_memory": True,
            },
        )
        assert resp2.status_code == 200, resp2.text

    # Fresh DB session — metadata.form_answers retains structured bundle
    with session_scope(sf) as s:
        reloaded = get_application_job(s, job.application_id)
        assert reloaded is not None
        stored = reloaded.metadata["form_answers"][field_label]
        assert isinstance(stored, dict)
        assert "files" in stored
        assert len(stored["files"]) == 2
        assert stored["files"][0]["path"] == str(cv)
        assert stored["files"][0]["kind"] == "cv"
        assert stored["files"][1]["kind"] == "transcript"
        # Order preserved
        assert [f["path"] for f in stored["files"]] == [str(cv), str(tr)]

        # Mapper consumes it
        field = FormField(
            selector=field_selector,
            name="unterlagen",
            label=field_label,
            type="file",
            required=True,
        )
        from universal_auto_applier.candidate_profile_loader import resolve_candidate_profile

        candidate = resolve_candidate_profile(reloaded.metadata)
        mapping = map_field(field, candidate, reloaded)
        assert mapping is not None
        assert mapping.document_bundle is not None
        assert len(mapping.document_bundle) == 2
        assert mapping.document_bundle[0].kind == "cv"
        assert mapping.document_bundle[1].kind == "transcript"

        # Executor would upload complete bundle (tested in execution test)
        # Here we just verify the bundle survives and is ordered
        assert [e.path for e in mapping.document_bundle] == [str(cv), str(tr)]

    engine.dispose()
