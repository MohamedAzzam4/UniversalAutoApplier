"""Tests for :mod:`universal_auto_applier.interventions.review`.

Covers review state creation, approval gate, submit safety, and integration
with form fill summaries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FillResult,
    FormFillSummary,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.interventions.review import (
    approve_review_state,
    check_submit_approval,
    create_review_state,
)


def _make_candidate() -> CandidateProfile:
    return CandidateProfile(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        phone="+49 123 456789",
    )


def _make_job(tmp_path: Path) -> ApplicationJob:
    cv = tmp_path / "cv.pdf"
    cover = tmp_path / "cover.pdf"
    cv.write_bytes(b"fake")
    cover.write_bytes(b"fake")
    url = "https://example.com/jobs/123"
    application_id = compute_application_id(
        platform="greenhouse", external_job_id="job-123", url=url
    )
    return ApplicationJob(
        application_id=application_id,
        platform=Platform.GREENHOUSE,
        source="linkedin",
        company="Example Corp",
        title="Software Engineer",
        url=url,
        score=4.1,
        verdict="apply",
        cv_pdf=str(cv),
        cover_letter_pdf=str(cover),
        status=ApplicationStatus.READY_TO_APPLY,
        external_job_id="job-123",
    )


class TestCreateReviewState:
    def test_creates_unapproved_state(self, tmp_path: Path) -> None:
        summary = FormFillSummary(total_fields=2, filled=2, results=[])
        state = create_review_state(
            application_id="job-123",
            company="Example Corp",
            title="Software Engineer",
            fill_summary=summary,
            final_action_detected="Submit application",
        )

        assert state.approved is False
        assert state.can_submit is False
        assert state.final_action_detected == "Submit application"

    def test_captures_unanswered_fields(self, tmp_path: Path) -> None:
        summary = FormFillSummary(
            total_fields=3,
            filled=1,
            intervention_needed=2,
            results=[
                FillResult(field_selector="#fn", field_type="text", status="filled", value="John"),
                FillResult(field_selector="#gpa", field_type="text", status="intervention_needed"),
                FillResult(
                    field_selector="#salary", field_type="text", status="intervention_needed"
                ),
            ],
        )
        state = create_review_state(
            application_id="job-123",
            fill_summary=summary,
        )

        assert len(state.unanswered_fields) == 2
        assert "#gpa" in state.unanswered_fields
        assert "#salary" in state.unanswered_fields

    def test_captures_documents(self) -> None:
        state = create_review_state(
            application_id="job-123",
            documents=["/tmp/cv.pdf", "/tmp/cover.pdf"],
        )

        assert state.documents == ["/tmp/cv.pdf", "/tmp/cover.pdf"]


class TestSubmitApproval:
    def test_submit_blocked_without_review_state(self) -> None:
        assert check_submit_approval(None) is False

    def test_submit_blocked_without_approval(self, tmp_path: Path) -> None:
        summary = FormFillSummary(total_fields=1, filled=1, results=[])
        state = create_review_state(application_id="job-123", fill_summary=summary)

        assert check_submit_approval(state) is False

    def test_submit_blocked_with_interventions(self, tmp_path: Path) -> None:
        summary = FormFillSummary(
            total_fields=2,
            filled=1,
            intervention_needed=1,
            results=[
                FillResult(field_selector="#fn", field_type="text", status="filled"),
                FillResult(field_selector="#gpa", field_type="text", status="intervention_needed"),
            ],
        )
        state = create_review_state(application_id="job-123", fill_summary=summary)

        # Cannot approve with interventions.
        with pytest.raises(ValueError, match="unresolved interventions"):
            approve_review_state(state, approval_id="approval-1")

        assert check_submit_approval(state) is False

    def test_submit_allowed_after_approval(self, tmp_path: Path) -> None:
        summary = FormFillSummary(
            total_fields=1,
            filled=1,
            intervention_needed=0,
            results=[
                FillResult(field_selector="#fn", field_type="text", status="filled", value="John"),
            ],
        )
        state = create_review_state(application_id="job-123", fill_summary=summary)

        approve_review_state(state, approval_id="approval-1")

        assert state.approved is True
        assert state.approval_id == "approval-1"
        assert state.approved_at is not None
        assert check_submit_approval(state) is True

    def test_can_submit_property(self, tmp_path: Path) -> None:
        summary = FormFillSummary(total_fields=1, filled=1, intervention_needed=0, results=[])
        state = create_review_state(application_id="job-123", fill_summary=summary)

        assert state.can_submit is False

        approve_review_state(state, approval_id="approval-1")
        assert state.can_submit is True

    def test_has_unresolved_interventions_property(self, tmp_path: Path) -> None:
        summary_with = FormFillSummary(total_fields=2, filled=1, intervention_needed=1, results=[])
        state_with = create_review_state(application_id="job-123", fill_summary=summary_with)
        assert state_with.has_unresolved_interventions is True

        summary_without = FormFillSummary(
            total_fields=1, filled=1, intervention_needed=0, results=[]
        )
        state_without = create_review_state(application_id="job-123", fill_summary=summary_without)
        assert state_without.has_unresolved_interventions is False


class TestReviewWithFillEngine:
    """Integration test: fill a form, create review state, verify safety."""

    def test_filled_form_can_be_approved(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.models import FormField

        fields = [
            FormField(
                selector="#fn", name="first_name", label="First name", type="text", required=True
            ),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))
        state = create_review_state(
            application_id="job-123",
            company="Example Corp",
            fill_summary=summary,
            final_action_detected="Submit application",
        )

        assert state.has_unresolved_interventions is False
        approve_review_state(state, approval_id="approval-1")
        assert check_submit_approval(state) is True

    def test_form_with_interventions_cannot_be_approved(self, tmp_path: Path) -> None:
        from universal_auto_applier.core.models import FormField

        fields = [
            FormField(selector="#gpa", name="gpa", label="College GPA", type="text", required=True),
        ]
        summary = fill_form(fields, _make_candidate(), _make_job(tmp_path))
        state = create_review_state(
            application_id="job-123",
            fill_summary=summary,
        )

        assert state.has_unresolved_interventions is True
        with pytest.raises(ValueError, match="unresolved interventions"):
            approve_review_state(state, approval_id="approval-1")
        assert check_submit_approval(state) is False
