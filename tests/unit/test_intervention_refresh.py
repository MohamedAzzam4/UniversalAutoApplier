"""Unit tests for existing-intervention metadata refresh.

These tests prove that ``create_intervention`` refreshes machine-generated
metadata on existing PENDING interventions (fixing the real-ATS defect
where stale empty options persisted after a re-run) while NEVER touching
resolved/edited/blocked interventions.

Test matrix:
- Existing PENDING intervention with options=[] becomes ["ja", "nein"].
- Existing PENDING salutation intervention receives all current options.
- Existing PENDING llm_metadata.available_options is refreshed.
- Existing PENDING confidence is refreshed.
- Existing PENDING page_url is refreshed.
- Existing PENDING suggested_answer is refreshed when new value is non-empty.
- Existing resolved/approved intervention is NOT modified.
- Existing edited intervention is NOT modified.
- Existing user-edited answer (suggested_answer on APPROVED) is NOT overwritten.
- Reprocessing remains idempotent (no duplicate rows, no spurious changes).
- API and dashboard return the refreshed options.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import (
    ApplicationStatus,
    InterventionKind,
    InterventionStatus,
    Platform,
)
from universal_auto_applier.interventions.store import (
    create_intervention,
    list_all_interventions,
    list_pending_interventions,
    resolve_intervention,
)
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import upsert_application_job
from universal_auto_applier.persistence.migrations import apply_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, port: int = 8030) -> Settings:
    settings = Settings(
        host="127.0.0.1",
        port=port,
        data_dir=tmp_path / "uaa_refresh",
        browser_headless=True,
        submit_mode="review",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    apply_migrations(build_engine_url(settings.data_dir / "uaa.sqlite"))
    return settings


def _make_job(tmp_path: Path, url: str = "https://example.com/job/refresh-1") -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="refresh-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Test",
        title="Engineer",
        url=url,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="refresh-1",
        metadata={},
    )


# ---------------------------------------------------------------------------
# 1. Existing PENDING intervention with options=[] becomes ["ja", "nein"]
# ---------------------------------------------------------------------------


class TestPendingOptionsBackfill:
    def test_empty_options_backfilled_to_german_options(self, tmp_path: Path) -> None:
        """An existing PENDING intervention with options=[] must be
        refreshed to options=["ja", "nein"] when create_intervention is
        called again with the full option list."""
        settings = _make_settings(tmp_path, port=8031)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            # First create: options=[] (simulates the old defect).
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with Python?",
                options=[],
                field_selector="lf-python-radio",
                confidence=None,
                llm_metadata=None,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert ivs[0].options == [], "Initial options should be empty"

        # Re-process: now with the full option list.
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with Python?",
                options=["ja", "nein"],
                field_selector="lf-python-radio",
                confidence=0.9,
                llm_metadata={
                    "available_options": ["ja", "nein"],
                    "category": "skills_experience",
                },
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert len(ivs) == 1, "No duplicate should be created"
        assert ivs[0].options == ["ja", "nein"], (
            f"Expected [ja, nein] after refresh, got {ivs[0].options!r}"
        )
        engine.dispose()


# ---------------------------------------------------------------------------
# 2. Existing PENDING salutation intervention receives all current options
# ---------------------------------------------------------------------------


class TestPendingSalutationRefresh:
    def test_salutation_options_refreshed(self, tmp_path: Path) -> None:
        """An existing PENDING salutation intervention with options=[]
        must be refreshed to the full salutation option list."""
        settings = _make_settings(tmp_path, port=8032)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        salutation_options = [
            "Please choose",
            "Not specified",
            "Mr.",
            "Ms.",
            "Diverse",
        ]

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salutation",
                options=[],
                field_selector="lf-salutation-select",
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].options == []

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salutation",
                options=salutation_options,
                field_selector="lf-salutation-select",
                llm_metadata={"available_options": salutation_options},
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert ivs[0].options == salutation_options, (
            f"Expected {salutation_options}, got {ivs[0].options!r}"
        )
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. Existing PENDING llm_metadata.available_options is refreshed
# ---------------------------------------------------------------------------


class TestPendingLlmMetadataRefresh:
    def test_llm_metadata_available_options_refreshed(self, tmp_path: Path) -> None:
        """An existing PENDING intervention with llm_metadata=None must
        have its llm_metadata refreshed, including available_options."""
        settings = _make_settings(tmp_path, port=8033)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with SPSS?",
                options=["ja", "nein"],
                field_selector="lf-spss-radio",
                llm_metadata=None,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].llm_metadata is None

        new_metadata = {
            "available_options": ["ja", "nein"],
            "evidence_summary": "no evidence",
            "category": "skills_experience",
            "risk_level": "medium",
            "requires_confirmation": True,
        }
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with SPSS?",
                options=["ja", "nein"],
                field_selector="lf-spss-radio",
                llm_metadata=new_metadata,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].llm_metadata is not None
        assert ivs[0].llm_metadata["available_options"] == ["ja", "nein"]
        assert ivs[0].llm_metadata["category"] == "skills_experience"
        engine.dispose()

    def test_confidence_refreshed(self, tmp_path: Path) -> None:
        """An existing PENDING intervention's confidence is refreshed."""
        settings = _make_settings(tmp_path, port=8034)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salary expectation?",
                field_selector="lf-salary",
                confidence=0.5,
            )

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salary expectation?",
                field_selector="lf-salary",
                confidence=0.9,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].confidence == 0.9
        engine.dispose()

    def test_page_url_refreshed(self, tmp_path: Path) -> None:
        """An existing PENDING intervention's page_url is refreshed (the
        field may have moved in a multi-step form)."""
        settings = _make_settings(tmp_path, port=8035)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Phone number?",
                field_selector="lf-phone",
                page_url="https://example.com/step1",
            )

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Phone number?",
                field_selector="lf-phone",
                page_url="https://example.com/step2",
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].page_url == "https://example.com/step2"
        engine.dispose()

    def test_suggested_answer_refreshed_when_non_empty(self, tmp_path: Path) -> None:
        """An existing PENDING intervention's suggested_answer is refreshed
        when the new value is non-empty (machine-generated answer update)."""
        settings = _make_settings(tmp_path, port=8036)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Years of experience?",
                field_selector="lf-years",
                suggested_answer=None,
            )

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Years of experience?",
                field_selector="lf-years",
                suggested_answer="5",
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].suggested_answer == "5"
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. Existing resolved/approved intervention is NOT modified
# ---------------------------------------------------------------------------


class TestResolvedInterventionPreservation:
    def test_approved_intervention_not_modified(self, tmp_path: Path) -> None:
        """An APPROVED intervention must NOT be modified by a re-run.
        Its status, options, suggested_answer, and resolved_at must
        remain exactly as the user left them."""
        settings = _make_settings(tmp_path, port=8037)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salary expectation?",
                field_selector="lf-salary",
                options=["50000"],
                suggested_answer="50000",
                confidence=0.7,
            )
            iv_id = row.intervention_id
            # User approves with a specific answer.
            resolve_intervention(
                session,
                iv_id,
                resolution=InterventionStatus.APPROVED,
                answer="55000",
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert str(ivs[0].status) == "approved"
        assert ivs[0].suggested_answer == "55000"
        original_resolved_at = ivs[0].resolved_at

        # Re-process with different data.
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salary expectation?",
                field_selector="lf-salary",
                options=["60000", "70000"],
                suggested_answer="60000",
                confidence=0.95,
                llm_metadata={"available_options": ["60000", "70000"]},
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert len(ivs) == 1, "No duplicate should be created"
        assert str(ivs[0].status) == "approved", "Status must remain approved"
        assert ivs[0].suggested_answer == "55000", "User-approved answer must NOT be overwritten"
        assert ivs[0].options == ["50000"], (
            "Options must NOT be refreshed on a resolved intervention"
        )
        assert ivs[0].confidence == 0.7, "Confidence must NOT be refreshed"
        assert ivs[0].resolved_at == original_resolved_at, "resolved_at must not change"
        engine.dispose()

    def test_edited_intervention_not_modified(self, tmp_path: Path) -> None:
        """An EDITED intervention must NOT be modified by a re-run."""
        settings = _make_settings(tmp_path, port=8038)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with Docker?",
                field_selector="lf-docker",
                options=["ja", "nein"],
                suggested_answer="ja",
            )
            iv_id = row.intervention_id
            resolve_intervention(
                session,
                iv_id,
                resolution=InterventionStatus.EDITED,
                answer="nein",
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert str(ivs[0].status) == "edited"
        assert ivs[0].suggested_answer == "nein"

        # Re-process.
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Do you have experience with Docker?",
                field_selector="lf-docker",
                options=["ja", "nein"],
                suggested_answer="ja",
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert str(ivs[0].status) == "edited"
        assert ivs[0].suggested_answer == "nein", "User-edited answer preserved"
        engine.dispose()

    def test_blocked_intervention_not_modified(self, tmp_path: Path) -> None:
        """A BLOCKED intervention must NOT be modified by a re-run."""
        settings = _make_settings(tmp_path, port=8039)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Criminal record?",
                field_selector="lf-criminal",
                options=["Yes", "No"],
                suggested_answer="No",
            )
            iv_id = row.intervention_id
            resolve_intervention(session, iv_id, resolution=InterventionStatus.BLOCKED)

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert str(ivs[0].status) == "blocked"

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Criminal record?",
                field_selector="lf-criminal",
                options=["Yes", "No"],
                suggested_answer="Yes",
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert str(ivs[0].status) == "blocked"
        assert ivs[0].suggested_answer == "No", "Blocked answer preserved"
        engine.dispose()

    def test_skipped_intervention_not_modified(self, tmp_path: Path) -> None:
        """A SKIPPED intervention must NOT be modified by a re-run."""
        settings = _make_settings(tmp_path, port=8040)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            row = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Optional question?",
                field_selector="lf-optional",
            )
            iv_id = row.intervention_id
            resolve_intervention(session, iv_id, resolution=InterventionStatus.SKIPPED)

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert str(ivs[0].status) == "skipped"

        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Optional question?",
                field_selector="lf-optional",
                options=["A", "B"],
                suggested_answer="A",
            )

        with session_scope(sf) as session:
            ivs = list_all_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert str(ivs[0].status) == "skipped"
        assert ivs[0].options == [], "Skipped intervention options not refreshed"
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. User-edited answer is NOT overwritten (PENDING with user-set answer)
# ---------------------------------------------------------------------------


class TestUserEditedAnswerPreservation:
    def test_pending_suggested_answer_refreshed_only_when_new_non_empty(
        self, tmp_path: Path
    ) -> None:
        """A PENDING intervention's suggested_answer is refreshed only
        when the new value is non-empty. If the re-run produces no
        suggested_answer (None), the existing machine-generated answer
        is preserved (not wiped to None)."""
        settings = _make_settings(tmp_path, port=8041)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Years of experience?",
                field_selector="lf-years",
                suggested_answer="5",
            )

        # Re-run with no suggested_answer (None) — existing "5" preserved.
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Years of experience?",
                field_selector="lf-years",
                suggested_answer=None,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert ivs[0].suggested_answer == "5", (
            "Existing suggested_answer must be preserved when new value is None"
        )
        engine.dispose()


# ---------------------------------------------------------------------------
# 6. Reprocessing remains idempotent
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_triple_reprocess_no_duplicates(self, tmp_path: Path) -> None:
        """Reprocessing the same intervention three times must produce
        exactly one row with the latest metadata."""
        settings = _make_settings(tmp_path, port=8042)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)

        # Process three times with evolving metadata.
        for opts in [[], ["ja"], ["ja", "nein"]]:
            with session_scope(sf) as session:
                create_intervention(
                    session,
                    application_id=job.application_id,
                    kind=InterventionKind.FIELD_ANSWER,
                    question="Python experience?",
                    field_selector="lf-python",
                    options=opts,
                )

        with session_scope(sf) as session:
            all_ivs = list_all_interventions(session, job.application_id)
        assert len(all_ivs) == 1, f"Expected 1 row, got {len(all_ivs)}"
        assert all_ivs[0].options == ["ja", "nein"], (
            "Options should reflect the latest non-empty value"
        )
        engine.dispose()

    def test_no_change_when_metadata_unchanged(self, tmp_path: Path) -> None:
        """When the metadata is identical, the refresh is a no-op (no
        spurious flush, no error)."""
        settings = _make_settings(tmp_path, port=8043)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Python experience?",
                field_selector="lf-python",
                options=["ja", "nein"],
                confidence=0.9,
            )
            original_created_at = list_pending_interventions(session, job.application_id)[
                0
            ].created_at

        # Re-process with identical metadata.
        with session_scope(sf) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Python experience?",
                field_selector="lf-python",
                options=["ja", "nein"],
                confidence=0.9,
            )

        with session_scope(sf) as session:
            ivs = list_pending_interventions(session, job.application_id)
        assert len(ivs) == 1
        assert ivs[0].options == ["ja", "nein"]
        assert ivs[0].confidence == 0.9
        assert ivs[0].created_at == original_created_at
        engine.dispose()


# ---------------------------------------------------------------------------
# 7. API and dashboard return the refreshed options
# ---------------------------------------------------------------------------


class TestApiReturnsRefreshedOptions:
    def test_api_returns_refreshed_options(self, tmp_path: Path) -> None:
        """The API must return the refreshed options after a re-run."""
        from fastapi.testclient import TestClient

        from universal_auto_applier.api.app import create_app
        from universal_auto_applier.persistence.models import Base

        settings = _make_settings(tmp_path, port=8044)
        job = _make_job(tmp_path)
        engine = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf = make_session_factory(engine)

        with session_scope(sf) as session:
            upsert_application_job(session, job)
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salutation",
                field_selector="lf-salutation",
                options=[],
            )
        engine.dispose()

        # Re-process with full options.
        engine2 = make_engine(build_engine_url(settings.data_dir / "uaa.sqlite"))
        sf2 = make_session_factory(engine2)
        salutation_options = ["Please choose", "Mr.", "Ms.", "Diverse"]
        with session_scope(sf2) as session:
            create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.FIELD_ANSWER,
                question="Salutation",
                field_selector="lf-salutation",
                options=salutation_options,
                llm_metadata={"available_options": salutation_options},
            )
        engine2.dispose()

        app = create_app(settings=settings)
        with TestClient(app) as client:
            Base.metadata.create_all(app.state.engine)
            response = client.get("/api/interventions")
            assert response.status_code == 200
            body = response.json()
            iv = next(i for i in body["interventions"] if "salutation" in i["question"].lower())
            assert iv["options"] == salutation_options, (
                f"API returned stale options: {iv['options']!r}"
            )
            assert iv["llm_metadata"]["available_options"] == salutation_options
