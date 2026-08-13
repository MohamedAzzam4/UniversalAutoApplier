"""Unit tests for synthetic profile and document generation (WQ-7)."""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.synthetic_profile import (
    SyntheticProfile,
    create_synthetic_documents,
    generate_synthetic_cover_letter,
    generate_synthetic_cv,
)


class TestSyntheticProfile:
    """The synthetic profile has no real PII."""

    def test_name_identifies_test_automation(self) -> None:
        """The name clearly identifies test automation."""
        profile = SyntheticProfile()
        assert "Test" in profile.first_name
        assert "Automation" in profile.last_name

    def test_email_uses_example_com(self) -> None:
        """The email uses the reserved example.com domain."""
        profile = SyntheticProfile()
        assert profile.email.endswith("@example.com")

    def test_phone_is_non_real(self) -> None:
        """The phone number uses the 555-0100 fictional prefix."""
        profile = SyntheticProfile()
        assert "555" in profile.phone
        assert "0100" in profile.phone

    def test_wq7_synthetic_flag(self) -> None:
        """The profile is marked as synthetic."""
        profile = SyntheticProfile()
        assert profile.wq7_synthetic is True

    def test_to_metadata_includes_synthetic_flag(self) -> None:
        """The metadata dict includes the wq7_synthetic flag."""
        profile = SyntheticProfile()
        metadata = profile.to_metadata()
        assert metadata["candidate_profile"]["wq7_synthetic"] is True

    def test_no_real_linkedin_account(self) -> None:
        """The LinkedIn URL is clearly synthetic."""
        profile = SyntheticProfile()
        assert "test-automation-dry-run" in profile.linkedin

    def test_no_sensitive_answers(self) -> None:
        """No salary, legal, or demographic answers are fabricated."""
        profile = SyntheticProfile()
        # The profile should not contain salary, visa status (beyond boolean),
        # or demographic data.
        metadata = profile.to_metadata()["candidate_profile"]
        assert "salary" not in metadata
        assert "disability" not in metadata
        assert "veteran" not in metadata
        assert "race" not in metadata
        assert "gender" not in metadata


class TestSyntheticDocuments:
    """Synthetic CV and cover letter are clearly marked as test data."""

    def test_cv_generated(self, tmp_path: Path) -> None:
        """A CV PDF is generated."""
        cv_path = generate_synthetic_cv(tmp_path / "cv.pdf")
        assert cv_path.exists()
        assert cv_path.suffix == ".pdf"

    def test_cover_letter_generated(self, tmp_path: Path) -> None:
        """A cover letter PDF is generated."""
        cover_path = generate_synthetic_cover_letter(tmp_path / "cover.pdf")
        assert cover_path.exists()
        assert cover_path.suffix == ".pdf"

    def test_cv_is_valid_pdf(self, tmp_path: Path) -> None:
        """The CV is a valid PDF (starts with %PDF)."""
        cv_path = generate_synthetic_cv(tmp_path / "cv.pdf")
        content = cv_path.read_bytes()
        assert content[:5] == b"%PDF-"

    def test_cover_is_valid_pdf(self, tmp_path: Path) -> None:
        """The cover letter is a valid PDF."""
        cover_path = generate_synthetic_cover_letter(tmp_path / "cover.pdf")
        content = cover_path.read_bytes()
        assert content[:5] == b"%PDF-"

    def test_cv_contains_test_data_marking(self, tmp_path: Path) -> None:
        """The CV contains visible TEST DATA marking."""
        cv_path = generate_synthetic_cv(tmp_path / "cv.pdf")
        content = cv_path.read_bytes()
        assert b"TEST DATA" in content
        assert b"AUTOMATION DRY RUN" in content
        assert b"NOT A REAL APPLICATION" in content

    def test_cover_contains_test_data_marking(self, tmp_path: Path) -> None:
        """The cover letter contains visible TEST DATA marking."""
        cover_path = generate_synthetic_cover_letter(tmp_path / "cover.pdf")
        content = cover_path.read_bytes()
        assert b"TEST DATA" in content
        assert b"AUTOMATION DRY RUN" in content

    def test_create_synthetic_documents(self, tmp_path: Path) -> None:
        """create_synthetic_documents generates both files."""
        cv_path, cover_path = create_synthetic_documents(tmp_path)
        assert cv_path.exists()
        assert cover_path.exists()
        assert cv_path != cover_path

    def test_documents_not_committed(self, tmp_path: Path) -> None:
        """Documents are created in a temp directory, not in the repo."""
        # This test verifies the documents are in tmp_path, not in the repo.
        cv_path, cover_path = create_synthetic_documents(tmp_path / "docs")
        assert tmp_path in cv_path.parents
        assert tmp_path in cover_path.parents
