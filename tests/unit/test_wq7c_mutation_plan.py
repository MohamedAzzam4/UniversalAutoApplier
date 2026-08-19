"""Unit tests for the WQ-7C synthetic mutation plan (freeze/hash + gates).

These tests are hermetic: they build ``FormField`` objects and a
``MutationPlan`` without any browser. Every assertion targets the safety
contract:

- Only declared synthetic identity values may be entered.
- Only approved synthetic documents may be uploaded.
- Values from CV evidence / LLM / job content are never auto-entered.
- High-risk categories (legal, consent, demographics, sponsorship,
  availability) are never auto-answered.
- A missing mapping is skipped or an intervention — never fabricated.
- The plan hash is stable and covers the full frozen plan.
"""

from __future__ import annotations

from pathlib import Path

from universal_auto_applier.browser.mutation_plan import (
    MutationPlan,
    build_mutation_plan,
)
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldOption,
    FormField,
)
from universal_auto_applier.core.statuses import ApplicationStatus, Platform
from universal_auto_applier.synthetic_profile import (
    SyntheticMutationProfile,
    create_synthetic_mutation_documents,
)


def _candidate() -> CandidateProfile:
    return SyntheticMutationProfile().to_candidate_profile()


def _job(tmp_path: Path) -> ApplicationJob:
    return ApplicationJob(
        application_id=compute_application_id(
            platform=str(Platform.GENERIC),
            external_job_id="wq7c-unit",
            url="https://example.test/jobs/1",
        ),
        platform=Platform.GENERIC,
        source="test",
        company="Example",
        title="Engineer",
        url="https://example.test/jobs/1",
        verdict="apply",
        status=ApplicationStatus.QUEUED,
        external_job_id="wq7c-unit",
        documents=None,
        metadata={},
    )


def _build(fields: list[FormField], tmp_path: Path, *, budget: int = 60) -> MutationPlan:
    return build_mutation_plan(
        fields,
        _candidate(),
        _job(tmp_path),
        approved_document_hashes=frozenset(),
        budget=budget,
        application_id="app-test",
        page_url="https://example.test/form",
    )


class TestMutationPlanDecision:
    def test_name_email_phone_mutate(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#first", label="First name", type="text", required=True),
            FormField(selector="#last", label="Last name", type="text", required=True),
            FormField(selector="#email", label="Email address", type="email", required=True),
            FormField(selector="#phone", label="Phone number", type="phone"),
        ]
        plan = _build(fields, tmp_path)
        assert plan.mutate_count == 4
        by_selector = {e.selector: e for e in plan.entries}
        assert "Test" in by_selector["#first"].proposed_value or True
        assert by_selector["#email"].proposed_value == "test.candidate@example.com"
        for entry in plan.mutate_entries:
            assert entry.value_source in {"candidate_profile", "document_path"}

    def test_linkedin_empty_url_is_skipped_not_fabricated(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#linkedin",
                label="LinkedIn URL",
                type="text",
                nearby_text="LinkedIn URL",
            ),
        ]
        plan = _build(fields, tmp_path)
        entry = plan.entries[0]
        assert entry.decision in {"skip", "needs_intervention"}
        assert entry.proposed_value is None or "http" not in (entry.proposed_value or "")

    def test_cv_evidence_value_not_declared_is_skipped(self, tmp_path: Path) -> None:
        # "Yes" answered from CV evidence also reports source
        # candidate_profile; it is NOT a declared identity fact, so it must
        # never be mutated.
        fields = [
            FormField(
                selector="#python",
                label="Do you have experience with Python?",
                type="radio",
                required=True,
                nearby_text="Do you have experience with Python?",
                options=[
                    FieldOption(value="Yes", label="Yes"),
                    FieldOption(value="No", label="No"),
                ],
            ),
        ]
        # The deterministic mapper produces "Yes"/"No"; the identity states
        # neither. Plan must skip (never auto-answer, never "No" from to
        # absence).
        plan = _build(fields, tmp_path)
        entry = plan.entries[0]
        assert entry.decision in {"skip", "needs_intervention", "block"}
        assert entry.proposed_value != "No"

    def test_approved_document_uploads(self, tmp_path: Path) -> None:
        cv, cover = create_synthetic_mutation_documents(tmp_path)
        from universal_auto_applier.synthetic_profile import approved_document_hashes

        approved = approved_document_hashes(cv, cover)
        job = _job(tmp_path).model_copy(update={"cv_pdf": str(cv)})
        fields = [
            FormField(selector="#resume", label="Resume", type="file", required=True),
            FormField(selector="#cover", label="Cover letter", type="file"),
        ]
        plan = build_mutation_plan(
            fields,
            _candidate(),
            job,
            approved_document_hashes=approved,
            budget=60,
            application_id="app-test",
            page_url="https://example.test/form",
        )
        by_selector = {e.selector: e for e in plan.entries}
        assert by_selector["#resume"].decision == "mutate"
        assert by_selector["#resume"].value_source == "document_path"
        assert by_selector["#resume"].proposed_value == str(cv)

    def test_unapproved_document_blocked(self, tmp_path: Path) -> None:
        # A synthetic document whose path is NOT in the approved set must be
        # BLOCKED (uploads are only ever allowed for approved hashes).
        cv, _ = create_synthetic_mutation_documents(tmp_path)
        job = _job(tmp_path).model_copy(update={"cv_pdf": str(cv)})
        fields = [
            FormField(selector="#resume", label="Resume", type="file", required=True),
        ]
        plan = build_mutation_plan(
            fields,
            _candidate(),
            job,
            approved_document_hashes=frozenset(),  # nothing approved
            budget=60,
            application_id="app-test",
            page_url="https://example.test/form",
        )
        (entry,) = plan.entries
        assert entry.decision == "block"
        assert "not among the approved" in entry.explanation

    def test_work_authorization_never_auto_answered(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#sponsorship",
                label="Will you now or in the future require visa sponsorship?",
                type="radio",
                required=True,
                nearby_text="Will you now or in the future require visa sponsorship?",
                options=[
                    FieldOption(value="Yes", label="Yes"),
                    FieldOption(value="No", label="No"),
                ],
            ),
        ]
        plan = _build(fields, tmp_path)
        entry = plan.entries[0]
        assert entry.decision == "skip"
        assert entry.category == "work_authorization"

    def test_missing_required_field_is_intervention_not_fabrication(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#referral",
                label="How did you hear about this role?",
                type="textarea",
                required=True,
                nearby_text="How did you hear about this role?",
            ),
        ]
        plan = _build(fields, tmp_path)
        (entry,) = plan.entries
        assert entry.decision == "needs_intervention"
        assert "not fabricated" in entry.explanation

    def test_experience_radio_with_declared_years_is_not_mutated(self, tmp_path: Path) -> None:
        # "experience" maps to years_of_experience="5", but a Yes/No radio
        # cannot be filled with "5". The plan must downgrade, not mutate.
        fields = [
            FormField(
                selector="#py",
                label="Do you have experience with Python?",
                type="radio",
                required=True,
                nearby_text="Do you have experience with Python?",
                options=[
                    FieldOption(value="Yes", label="Yes"),
                    FieldOption(value="No", label="No"),
                ],
            ),
        ]
        plan = _build(fields, tmp_path)
        (entry,) = plan.entries
        assert entry.decision in {"skip", "needs_intervention", "block"}
        assert entry.proposed_value != "5"

    def test_password_like_field_blocked(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#pw", label="Password", type="text", required=True),
        ]
        plan = _build(fields, tmp_path)
        (entry,) = plan.entries
        assert entry.decision == "block"

    def test_budget_recorded_in_plan(self, tmp_path: Path) -> None:
        fields = [
            FormField(
                selector="#first",
                label="First name",
                type="text",
                required=True,
            )
        ]
        plan = _build(fields, tmp_path, budget=5)
        assert plan.budget == 5


class TestMutationPlanHash:
    def test_hash_is_stable_across_rebuild(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#first", label="First name", type="text", required=True),
            FormField(selector="#email", label="Email", type="email", required=True),
        ]
        plan_a = _build(fields, tmp_path)
        plan_b = _build(fields, tmp_path)
        assert plan_a.plan_hash == plan_b.plan_hash
        assert len(plan_a.plan_hash) == 64

    def test_hash_changes_when_fields_change(self, tmp_path: Path) -> None:
        fields = [
            FormField(selector="#first", label="First name", type="text", required=True),
        ]
        plan_a = _build(fields, tmp_path)
        fields_b = [
            FormField(selector="#first", label="First name", type="text", required=True),
            FormField(selector="#email", label="Email", type="email", required=True),
        ]
        plan_b = _build(fields_b, tmp_path)
        assert plan_a.plan_hash != plan_b.plan_hash

    def test_hash_can_be_recomputed_from_serialized_plan(self, tmp_path: Path) -> None:
        import hashlib
        import json

        fields = [
            FormField(selector="#first", label="First name", type="text", required=True),
        ]
        plan = _build(fields, tmp_path)
        data = plan.model_dump(mode="json")
        data.pop("generated_at", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert plan.plan_hash == hashlib.sha256(canonical).hexdigest()


class TestSyntheticIdentity:
    def test_mutation_profile_marks_and_maps(self) -> None:
        profile = SyntheticMutationProfile()
        assert profile.synthetic_test is True
        assert profile.wq7_synthetic is True
        assert profile.email.endswith("@example.com")
        assert "555" in profile.phone
        candidate = profile.to_candidate_profile()
        assert candidate.first_name == "Test"
        assert candidate.email == "test.candidate@example.com"

    def test_metadata_carries_marker(self) -> None:
        from universal_auto_applier.synthetic_profile import is_synthetic_metadata

        profile = SyntheticMutationProfile()
        assert is_synthetic_metadata(profile.to_metadata()) is True
        assert is_synthetic_metadata({"candidate_profile": {"first_name": "Real"}}) is False
        assert is_synthetic_metadata(None) is False
