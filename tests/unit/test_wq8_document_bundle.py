"""WQ-8 document bundle — mapping, persistence, and validation."""

from pathlib import Path

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormField,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.field_mapper import map_field
from universal_auto_applier.form_engine.fill_engine import fill_form


def _job(tmp_path: Path, metadata: dict | None = None) -> ApplicationJob:
    url = "https://example.test/jobs/bundle-1"
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="bundle-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Example",
        title="Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.QUEUED,
        external_job_id="bundle-1",
        metadata=metadata or {},
    )


def _file_field(label: str = "Vollständige Bewerbungsunterlagen") -> FormField:
    return FormField(
        selector="lf-bundle",
        name="unterlagen",
        label=label,
        type="file",
        required=True,
    )


def _text_field(label: str = "Vorname") -> FormField:
    return FormField(selector="lf-text", name="vorname", label=label, type="text")


class TestScalarStillWorks:
    def test_scalar_single_file_still_maps(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv content")
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": str(cv)}}
        )
        mapping = map_field(_file_field(), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.value == str(cv)
        assert mapping.document_bundle is None

    def test_scalar_text_still_maps(self, tmp_path: Path) -> None:
        job = _job(tmp_path, metadata={"form_answers": {"Vorname": "Alice"}})
        mapping = map_field(_text_field("Vorname"), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.value == "Alice"
        assert mapping.document_bundle is None

    def test_scalar_file_fill_validates_exists(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": str(cv)}}
        )
        summary = fill_form([_file_field()], CandidateProfile(first_name="A"), job)
        assert summary.filled == 1
        assert summary.intervention_needed == 0


class TestStructuredOneFileBundle:
    def test_one_file_bundle_via_files_key(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv one")
        bundle = {"files": [{"path": str(cv), "kind": "cv"}]}
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        mapping = map_field(_file_field(), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.document_bundle is not None
        assert len(mapping.document_bundle) == 1
        assert mapping.document_bundle[0].path == str(cv)
        assert mapping.document_bundle[0].kind == "cv"
        assert mapping.value == str(cv)

    def test_one_file_bundle_via_list(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        bundle = [{"path": str(cv), "kind": "cv"}]
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        mapping = map_field(_file_field(), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.document_bundle is not None
        assert len(mapping.document_bundle) == 1

    def test_one_file_bundle_fill_preserves_bundle(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        bundle = {"files": [{"path": str(cv), "kind": "cv"}]}
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        summary = fill_form([_file_field()], CandidateProfile(), job)
        assert summary.filled == 1
        assert summary.results[0].document_bundle is not None
        assert len(summary.results[0].document_bundle) == 1


class TestStructuredTwoFileBundle:
    def test_cv_transcript_bundle_survives_mapping(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv content for hash")
        tr = tmp_path / "transcript.pdf"
        tr.write_bytes(b"transcript content")
        bundle = {
            "files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr), "kind": "transcript"}]
        }
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        mapping = map_field(_file_field(), CandidateProfile(), job)
        assert mapping is not None
        bundle_out = mapping.document_bundle
        assert bundle_out is not None
        assert len(bundle_out) == 2
        assert bundle_out[0].path == str(cv) and bundle_out[0].kind == "cv"
        assert bundle_out[1].path == str(tr) and bundle_out[1].kind == "transcript"
        # Order preserved
        assert [e.path for e in bundle_out] == [str(cv), str(tr)]

    def test_bundle_fill_validates_every_path(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        tr = tmp_path / "transcript.pdf"
        tr.write_bytes(b"transcript")
        bundle = {
            "files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr), "kind": "transcript"}]
        }
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        summary = fill_form([_file_field()], CandidateProfile(), job)
        assert summary.filled == 1
        assert summary.results[0].document_bundle is not None
        assert len(summary.results[0].document_bundle) == 2

    def test_non_file_scalar_unaffected_by_bundle_logic(self, tmp_path: Path) -> None:
        # Text field with scalar string should not be affected by bundle parsing
        job = _job(tmp_path, metadata={"form_answers": {"Vorname": "Bob"}})
        mapping = map_field(_text_field("Vorname"), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.value == "Bob"
        assert mapping.document_bundle is None
        # Text field with structured dict should not map (fail closed, not bundle)
        job2 = _job(
            tmp_path,
            metadata={"form_answers": {"Vorname": {"files": [{"path": "/tmp/x", "kind": "cv"}]}}},
        )
        mapping2 = map_field(_text_field("Vorname"), CandidateProfile(), job2)
        # For non-file fields, structured answer is not allowed → no mapping (intervention)
        assert mapping2 is None


class TestBundleValidationFailsClosed:
    def test_malformed_bundle_empty_list_fails(self, tmp_path: Path) -> None:
        job = _job(tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": []}})
        mapping = map_field(_file_field(), CandidateProfile(), job)
        # Empty list is not a valid bundle → falls back to scalar handling which fails (empty string)
        assert mapping is None

    def test_malformed_bundle_missing_path_fails(self, tmp_path: Path) -> None:
        bundle = {"files": [{"kind": "cv"}]}  # no path
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        mapping = map_field(_file_field(), CandidateProfile(), job)
        # No valid entries → no mapping
        assert mapping is None

    def test_missing_path_fails_closed_via_fill(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        missing = str(tmp_path / "missing.pdf")
        bundle = {
            "files": [{"path": str(cv), "kind": "cv"}, {"path": missing, "kind": "transcript"}]
        }
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        summary = fill_form([_file_field()], CandidateProfile(), job)
        # fill should mark intervention_needed because one file missing
        assert summary.intervention_needed == 1
        assert summary.filled == 0

    def test_directory_path_fails_closed(self, tmp_path: Path) -> None:
        cv = tmp_path / "cv.pdf"
        cv.write_bytes(b"cv")
        subdir = tmp_path / "adir"
        subdir.mkdir()
        bundle = {
            "files": [{"path": str(cv), "kind": "cv"}, {"path": str(subdir), "kind": "transcript"}]
        }
        job = _job(
            tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
        )
        summary = fill_form([_file_field()], CandidateProfile(), job)
        assert summary.intervention_needed == 1
        assert summary.filled == 0


class TestBundlePersistenceViaInterventionAPI:
    """Official API/edit path → persist bundle → fresh DB reload → mapper consumes."""

    def test_bundle_survives_form_answers_persistence(self, tmp_path: Path) -> None:

        from universal_auto_applier.persistence.db import (
            make_engine,
            make_session_factory,
            session_scope,
        )
        from universal_auto_applier.persistence.job_repository import (
            get_application_job,
            upsert_application_job,
        )

        # Create a temporary DB
        db_path = tmp_path / "test_bundle.sqlite"
        from universal_auto_applier.persistence.db import build_engine_url

        url = build_engine_url(db_path)
        from universal_auto_applier.persistence.migrations import apply_migrations

        apply_migrations(url)
        engine = make_engine(url)
        factory = make_session_factory(engine)
        try:
            cv = tmp_path / "cv.pdf"
            cv.write_bytes(b"cv bundle persist")
            tr = tmp_path / "transcript.pdf"
            tr.write_bytes(b"transcript bundle persist")
            bundle = {
                "files": [{"path": str(cv), "kind": "cv"}, {"path": str(tr), "kind": "transcript"}]
            }
            job = _job(
                tmp_path, metadata={"form_answers": {"Vollständige Bewerbungsunterlagen": bundle}}
            )
            # Persist via upsert
            with session_scope(factory) as s:
                upsert_application_job(s, job)
            # Fresh DB session reload
            with session_scope(factory) as s:
                reloaded = get_application_job(s, job.application_id)
                assert reloaded is not None
                assert "form_answers" in reloaded.metadata
                stored = reloaded.metadata["form_answers"]["Vollständige Bewerbungsunterlagen"]
                # Must be structured, not stringified
                assert isinstance(stored, dict)
                assert "files" in stored
                assert len(stored["files"]) == 2
                assert stored["files"][0]["path"] == str(cv)
                assert stored["files"][0]["kind"] == "cv"
                assert stored["files"][1]["kind"] == "transcript"
                # Order preserved
                assert [f["path"] for f in stored["files"]] == [str(cv), str(tr)]
                # Mapper consumes it
                mapping = map_field(_file_field(), CandidateProfile(), reloaded)
                assert mapping is not None
                assert mapping.document_bundle is not None
                assert len(mapping.document_bundle) == 2
                assert mapping.document_bundle[0].kind == "cv"
        finally:
            engine.dispose()
