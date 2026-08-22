"""WQ-8 Phase A must reject synthetic candidate snapshots."""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.synthetic_profile import SyntheticMutationProfile, is_synthetic_metadata


def test_is_synthetic_metadata_true_for_wq7c() -> None:
    meta = SyntheticMutationProfile().to_metadata()
    assert is_synthetic_metadata(meta) is True


def test_is_synthetic_metadata_false_for_real() -> None:
    real_meta = {
        "candidate_profile": {
            "full_name": "Test Candidate",
            "email": "test.candidate@example.com",
            "first_name": "Test",
            "last_name": "Candidate",
        }
    }
    assert is_synthetic_metadata(real_meta) is False
    assert is_synthetic_metadata({}) is False
    assert is_synthetic_metadata(None) is False  # type: ignore[arg-type]


def test_wq8_phase_a_rejects_synthetic_job(tmp_path: Path) -> None:
    import argparse

    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from universal_auto_applier.cli import _live_dry_run
    from universal_auto_applier.config import Settings
    from universal_auto_applier.core.identity import compute_application_id
    from universal_auto_applier.persistence.db import (
        build_engine_url,
        make_session_factory,
        session_scope,
    )
    from universal_auto_applier.persistence.migrations import apply_migrations
    from universal_auto_applier.persistence.models import ApplicationJobRow
    from universal_auto_applier.synthetic_profile import SyntheticMutationProfile

    # Use the Settings-expected DB location: <data_dir>/uaa.sqlite
    data_dir = tmp_path / "uaa_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_url = build_engine_url(data_dir / "uaa.sqlite")
    engine = create_engine(db_url, future=True, poolclass=NullPool)
    apply_migrations(db_url)
    factory = make_session_factory(engine)

    synth_meta = SyntheticMutationProfile().to_metadata()
    url = "https://example.com/jobs/synth"
    app_id = compute_application_id(platform="unknown", external_job_id=None, url=url)

    (tmp_path / "cv.pdf").write_bytes(b"%PDF")
    (tmp_path / "cover.pdf").write_bytes(b"%PDF")

    with session_scope(factory) as s:
        row = ApplicationJobRow(
            application_id=app_id,
            platform="unknown",
            source="test",
            company="SynthCo",
            title="Synth Role",
            url=url,
            location=None,
            job_description="desc",
            score=4.0,
            verdict="apply",
            cv_pdf=str(tmp_path / "cv.pdf"),
            cover_letter_pdf=str(tmp_path / "cover.pdf"),
            status="ready_to_apply",
            metadata_json=synth_meta,
        )
        s.add(row)

    settings = Settings(data_dir=data_dir, wq8_phase_a=True)
    args = argparse.Namespace(
        application_id=app_id,
        start_url=None,
        artifacts_dir=None,
        profile_dir=None,
        ephemeral_profile=True,
        headless=True,
        channel=None,
        timeout_ms=None,
        max_steps=None,
        wq8_phase_a=True,
    )
    rc = _live_dry_run(settings, args)
    assert rc == 2
    engine.dispose()
