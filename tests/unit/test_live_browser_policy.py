"""Unit tests for live navigation and custom-question answer policy."""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.browser.live_runner import LiveBrowserConfig
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    ApplicationJobDocuments,
    CandidateProfile,
    FieldOption,
    FormField,
)
from universal_auto_applier.core.statuses import (
    ApplicationStatus,
    ClickableClassification,
    Platform,
)
from universal_auto_applier.form_engine.field_mapper import map_field
from universal_auto_applier.navigator.apply_path_finder import choose_safe_classification


def _job(
    tmp_path: Path,
    *,
    metadata: dict | None = None,
    cv_text: str | None = None,
    cover_text: str | None = None,
) -> ApplicationJob:
    url = "https://example.test/jobs/1"
    documents = None
    cv_md_value = None
    cover_md_value = None
    if cv_text is not None:
        cv_md = tmp_path / "cv.md"
        cv_md.write_text(cv_text, encoding="utf-8")
        cv_md_value = str(cv_md)
    if cover_text is not None:
        cover_md = tmp_path / "cover.md"
        cover_md.write_text(cover_text, encoding="utf-8")
        cover_md_value = str(cover_md)
    if cv_md_value or cover_md_value:
        documents = ApplicationJobDocuments(cv_md=cv_md_value, cover_letter_md=cover_md_value)
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC), external_job_id="live-policy-1", url=url
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Example",
        title="Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.QUEUED,
        external_job_id="live-policy-1",
        documents=documents,
        metadata=metadata or {},
    )


def _yes_no_field(question: str) -> FormField:
    return FormField(
        selector="#question",
        name="question",
        label=question,
        nearby_text=question,
        type="radio",
        required=True,
        options=[FieldOption(value="Yes", label="Yes"), FieldOption(value="No", label="No")],
    )


class TestNavigationPolicy:
    def test_apply_has_priority_over_continue(self) -> None:
        result = choose_safe_classification(
            [ClickableClassification.SAFE_CONTINUE, ClickableClassification.SAFE_APPLY],
            allow_apply=True,
            allow_continue=True,
        )
        assert result == ClickableClassification.SAFE_APPLY

    def test_form_phase_only_allows_continue(self) -> None:
        result = choose_safe_classification(
            [ClickableClassification.SAFE_APPLY, ClickableClassification.SAFE_CONTINUE],
            allow_apply=False,
            allow_continue=True,
        )
        assert result == ClickableClassification.SAFE_CONTINUE

    def test_submit_and_unknown_are_never_selected(self) -> None:
        result = choose_safe_classification(
            [
                ClickableClassification.DANGEROUS_SUBMIT,
                ClickableClassification.UNKNOWN,
            ],
            allow_apply=True,
            allow_continue=True,
        )
        assert result is None


class TestQuestionAnswerPolicy:
    def test_explicit_metadata_answer_is_used(self, tmp_path: Path) -> None:
        job = _job(
            tmp_path,
            metadata={"question_answers": {"Do you know SPSS?": "No"}},
        )
        mapping = map_field(_yes_no_field("Do you know SPSS?"), CandidateProfile(), job)
        assert mapping is not None
        assert mapping.value == "No"
        assert mapping.source == "application_job"

    def test_positive_cv_evidence_can_answer_yes(self, tmp_path: Path) -> None:
        job = _job(tmp_path, cv_text="Python automation and FastAPI development")
        mapping = map_field(
            _yes_no_field("Do you have experience with Python?"), CandidateProfile(), job
        )
        assert mapping is not None
        assert mapping.value == "Yes"
        assert mapping.source == "candidate_profile"

    def test_missing_evidence_never_invents_no(self, tmp_path: Path) -> None:
        job = _job(tmp_path, cv_text="Python automation")
        mapping = map_field(
            _yes_no_field("Do you have experience with SPSS?"), CandidateProfile(), job
        )
        assert mapping is None

    def test_required_marker_does_not_break_name_mapping(self, tmp_path: Path) -> None:
        field = FormField(selector="#first", label="First name *", type="text", required=True)
        mapping = map_field(field, CandidateProfile(first_name="Mohamed"), _job(tmp_path))
        assert mapping is not None
        assert mapping.value == "Mohamed"

    def test_german_skill_question_uses_positive_cv_evidence(self, tmp_path: Path) -> None:
        job = _job(tmp_path, cv_text="Python automation and FastAPI development")
        mapping = map_field(
            _yes_no_field("Hast du bereits Erfahrung mit Python?"),
            CandidateProfile(),
            job,
        )
        assert mapping is not None
        assert mapping.value == "Yes"

    def test_cover_letter_textarea_uses_tailored_markdown(self, tmp_path: Path) -> None:
        job = _job(tmp_path, cover_text="# Cover Letter\n\nI am interested in this role.")
        field = FormField(selector="#cover", label="Cover letter", type="textarea")
        mapping = map_field(field, CandidateProfile(), job)
        assert mapping is not None
        assert mapping.value == "Cover Letter\n\nI am interested in this role."


class TestLiveBrowserConfig:
    def test_rejects_invalid_limits(self, tmp_path: Path) -> None:
        try:
            LiveBrowserConfig(artifacts_root=tmp_path, timeout_ms=999)
        except ValueError as exc:
            assert "timeout_ms" in str(exc)
        else:
            raise AssertionError("expected timeout validation")

        try:
            LiveBrowserConfig(artifacts_root=tmp_path, max_steps=0)
        except ValueError as exc:
            assert "max_steps" in str(exc)
        else:
            raise AssertionError("expected max_steps validation")
