"""Unit tests for the candidate truth ledger.

Per the llm-question-resolution workpackage, tests cover:
- Fact provenance (every fact has a source).
- Explicit user facts override inferred facts.
- Contradictory facts are detected.
- Document reading (CV, cover letter).
- Evidence summary for LLM grounding.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.llm.truth_ledger import (
    Fact,
    build_ledger,
)


def _make_job(
    tmp_path: Path,
    *,
    metadata: dict | None = None,
    cv_md: str | None = None,
    cover_md: str | None = None,
) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"%PDF fake")
    cover.write_bytes(b"%PDF fake")
    url = "https://boards.greenhouse.io/example/jobs/1"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="test-1", url=url
    )
    documents = None
    if cv_md or cover_md:
        documents = ApplicationJobDocuments(cv_md=cv_md, cover_letter_md=cover_md)
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="Test Corp",
        title="Engineer",
        url=url,
        score=4.5,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.QUEUED,
        external_job_id="test-1",
        metadata=metadata or {},
        documents=documents,
    )


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        full_name="John Doe",
        email="john@example.com",
        phone="+49 123",
        city="Berlin",
        country="Germany",
    )


class TestFactProvenance:
    def test_every_fact_has_source(self, tmp_path: Path) -> None:
        """Every fact in the ledger must have a non-empty source."""
        job = _make_job(
            tmp_path,
            metadata={"candidate_profile": {"first_name": "John", "email": "john@example.com"}},
        )
        candidate = _make_candidate()
        ledger = build_ledger(job, candidate)
        for fact in ledger.facts:
            assert fact.source, f"Fact has no source: {fact}"
            assert fact.fact, f"Fact has empty text: {fact}"

    def test_candidate_profile_facts_have_provenance(self, tmp_path: Path) -> None:
        job = _make_job(
            tmp_path,
            metadata={"candidate_profile": {"first_name": "John"}},
        )
        ledger = build_ledger(job, _make_candidate())
        profile_facts = [f for f in ledger.facts if f.source == "candidate_profile"]
        assert len(profile_facts) > 0
        for fact in profile_facts:
            assert fact.source == "candidate_profile"
            assert fact.source_ref  # field name is set


class TestExplicitFactsOverride:
    def test_explicit_metadata_answer_included(self, tmp_path: Path) -> None:
        """Explicit per-job answers are included with high confidence."""
        job = _make_job(
            tmp_path,
            metadata={"question_answers": {"Do you have experience with SPSS?": "No"}},
        )
        ledger = build_ledger(job, _make_candidate())
        explicit_facts = [f for f in ledger.facts if f.source == "application_metadata"]
        assert len(explicit_facts) >= 1
        assert any("SPSS" in f.fact for f in explicit_facts)
        assert all(f.confidence == 1.0 for f in explicit_facts)

    def test_answer_memory_facts_highest_priority(self, tmp_path: Path) -> None:
        """Answer memory facts are included and have confidence 1.0."""
        job = _make_job(tmp_path)
        memory_facts = [
            Fact(
                fact="Answer to 'Relocate?': Yes",
                source="answer_memory",
                source_ref="Relocate?",
                confidence=1.0,
            )
        ]
        ledger = build_ledger(job, _make_candidate(), answer_memory_facts=memory_facts)
        memory_in_ledger = [f for f in ledger.facts if f.source == "answer_memory"]
        assert len(memory_in_ledger) == 1
        assert memory_in_ledger[0].confidence == 1.0


class TestContradictionDetection:
    def test_contradiction_detected(self, tmp_path: Path) -> None:
        """Two explicit facts with different answers for the same question are flagged."""
        job = _make_job(
            tmp_path,
            metadata={
                "question_answers": {"Relocate?": "Yes"},
                "application_answers": {"Relocate?": "No"},
            },
        )
        ledger = build_ledger(job, _make_candidate())
        assert len(ledger.contradictions) >= 1

    def test_no_contradiction_when_answers_match(self, tmp_path: Path) -> None:
        job = _make_job(
            tmp_path,
            metadata={
                "question_answers": {"Relocate?": "Yes"},
                "application_answers": {"Relocate?": "Yes"},
            },
        )
        ledger = build_ledger(job, _make_candidate())
        assert len(ledger.contradictions) == 0


class TestDocumentReading:
    def test_cv_markdown_read(self, tmp_path: Path) -> None:
        cv_md = tmp_path / "cv.md"
        cv_md.write_text(
            "# John Doe\n\nPython developer with 5 years experience.", encoding="utf-8"
        )
        job = _make_job(tmp_path, cv_md=str(cv_md))
        ledger = build_ledger(job, _make_candidate())
        cv_facts = [f for f in ledger.facts if f.source == "cv_markdown"]
        assert len(cv_facts) == 1
        assert "Python" in cv_facts[0].fact

    def test_cover_letter_markdown_read(self, tmp_path: Path) -> None:
        cover_md = tmp_path / "cover.md"
        cover_md.write_text("I am excited to apply for this role.", encoding="utf-8")
        job = _make_job(tmp_path, cover_md=str(cover_md))
        ledger = build_ledger(job, _make_candidate())
        cover_facts = [f for f in ledger.facts if f.source == "cover_letter_markdown"]
        assert len(cover_facts) == 1
        assert "excited" in cover_facts[0].fact.lower()

    def test_missing_document_does_not_crash(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, cv_md="/nonexistent/cv.md", cover_md="/nonexistent/cover.md")
        ledger = build_ledger(job, _make_candidate())
        # No CV/cover facts, but no crash either.
        cv_facts = [f for f in ledger.facts if f.source == "cv_markdown"]
        assert len(cv_facts) == 0


class TestEvidenceSummary:
    def test_summary_contains_facts(self, tmp_path: Path) -> None:
        job = _make_job(
            tmp_path,
            metadata={"candidate_profile": {"first_name": "John", "email": "john@example.com"}},
        )
        ledger = build_ledger(job, _make_candidate())
        summary = ledger.to_evidence_summary()
        assert "candidate_profile" in summary
        assert "John" in summary

    def test_summary_truncates(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path, metadata={"candidate_profile": {"a": "1"}})
        # Add many facts.
        ledger = build_ledger(job, _make_candidate())
        summary = ledger.to_evidence_summary(max_facts=2)
        # Should only contain 2 facts.
        assert summary.count("[") <= 2


class TestFactsForSubject:
    def test_facts_for_subject_returns_matches(self, tmp_path: Path) -> None:
        job = _make_job(
            tmp_path,
            metadata={"candidate_profile": {"first_name": "John", "email": "john@example.com"}},
        )
        ledger = build_ledger(job, _make_candidate())
        matches = ledger.facts_for_subject("email")
        assert len(matches) >= 1
        assert any("john@example.com" in f.fact for f in matches)

    def test_facts_for_subject_no_match(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        ledger = build_ledger(job, _make_candidate())
        matches = ledger.facts_for_subject("nonexistent_subject_xyz")
        assert len(matches) == 0


class TestHasExplicitFactFor:
    def test_explicit_fact_found(self, tmp_path: Path) -> None:
        job = _make_job(
            tmp_path,
            metadata={"question_answers": {"Do you have experience with SPSS?": "No"}},
        )
        ledger = build_ledger(job, _make_candidate())
        fact = ledger.has_explicit_fact_for("Do you have experience with SPSS?")
        assert fact is not None
        assert "SPSS" in fact.fact

    def test_no_explicit_fact(self, tmp_path: Path) -> None:
        job = _make_job(tmp_path)
        ledger = build_ledger(job, _make_candidate())
        fact = ledger.has_explicit_fact_for("Random question?")
        assert fact is None
