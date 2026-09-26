"""API/store contract tests for exact field correction and safe resume."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings
from universal_auto_applier.core.identity import compute_application_id
from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
from universal_auto_applier.core.statuses import (
    ApplicationStatus,
    InterventionKind,
    Platform,
)
from universal_auto_applier.form_engine.field_mapper import map_field
from universal_auto_applier.interventions.store import create_intervention
from universal_auto_applier.persistence.db import (
    build_engine_url,
    make_engine,
    make_session_factory,
    session_scope,
)
from universal_auto_applier.persistence.job_repository import (
    get_application_job,
    update_application_status,
    upsert_application_job,
)
from universal_auto_applier.persistence.migrations import apply_migrations
from universal_auto_applier.persistence.models import Base
from universal_auto_applier.persistence.pipeline_run_repository import create_pipeline_run
from universal_auto_applier.submission.models import (
    SubmissionSnapshot,
    SubmissionSnapshotField,
)
from universal_auto_applier.submission.store import (
    create_approval,
    get_active_approval,
    get_latest_approval,
)

_CORRECTIONS_KEY = "_uaa_field_corrections"


@contextmanager
def _api(tmp_path: Path) -> Iterator[tuple[TestClient, Any]]:
    settings = Settings(
        host="127.0.0.1",
        port=8421,
        data_dir=tmp_path / "field_correction_api",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=False,
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine_url = build_engine_url(settings.data_dir / "uaa.sqlite")
    apply_migrations(engine_url)
    engine = make_engine(engine_url)
    session_factory = make_session_factory(engine)

    app = create_app(settings=settings)
    app.state.engine = engine
    app.state.session_factory = session_factory
    Base.metadata.create_all(engine)
    try:
        with TestClient(app) as client:
            yield client, session_factory
    finally:
        engine.dispose()


def _make_job(external_id: str) -> ApplicationJob:
    url = f"https://example.test/jobs/{external_id}"
    return ApplicationJob(
        application_id=compute_application_id(
            platform=Platform.GENERIC.value,
            external_job_id=external_id,
            url=url,
        ),
        platform=Platform.GENERIC,
        source="field-correction-api-test",
        company="Example Corp",
        title="Engineer",
        url=url,
        verdict="apply",
        status=ApplicationStatus.NEEDS_USER_INPUT,
        external_job_id=external_id,
        metadata={"producer_marker": external_id},
    )


def _seed_case(
    session_factory: Any,
    external_id: str,
    *,
    with_unknown_http_blocker: bool = False,
) -> dict[str, Any]:
    job = _make_job(external_id)
    field_token = f"field-token-{external_id}"
    source_field_token = f"source-token-{external_id}"
    step_identity = f"step-{external_id}"
    snapshot = SubmissionSnapshot(
        application_id=job.application_id,
        application_url=job.url,
        fields=[
            SubmissionSnapshotField(
                field_token=field_token,
                source_field_token=source_field_token,
                step_identity=step_identity,
                label="Reference code",
                field_type="text",
                filled_value="",
                status="intervention_needed",
                required=True,
            )
        ],
        pending_intervention_count=1,
        unresolved_required_field_count=1,
        final_boundary_confirmed=True,
        completed_form_step_count=1,
        form_progress_fingerprint=f"progress-{external_id}",
    ).with_hashes()

    with session_scope(session_factory) as session:
        upsert_application_job(session, job)
        approval = create_approval(
            session,
            application_id=job.application_id,
            snapshot=snapshot,
        )
        field_intervention = create_intervention(
            session,
            application_id=job.application_id,
            kind=InterventionKind.FIELD_ANSWER,
            question="Reference code",
            field_selector=field_token,
            llm_metadata={
                "field_label": "Reference code",
                "field_token": field_token,
                "source_field_token": source_field_token,
                "step_identity": step_identity,
                "form_progress_fingerprint": snapshot.form_progress_fingerprint,
                "snapshot_hash": snapshot.snapshot_hash,
                "field_type": "text",
                "unresolved_reason": "intervention_needed",
                "required": True,
            },
        )
        unknown_intervention = None
        if with_unknown_http_blocker:
            unknown_intervention = create_intervention(
                session,
                application_id=job.application_id,
                kind=InterventionKind.HTTP_REQUEST_OUTCOME_UNKNOWN,
                question="A preparation request had an unknown outcome",
                field_selector="unknown-request-outcome",
                llm_metadata={"snapshot_hash": snapshot.snapshot_hash},
            )

    return {
        "job": job,
        "snapshot": snapshot,
        "approval_id": approval.approval_id,
        "intervention_id": field_intervention.intervention_id,
        "unknown_intervention_id": (
            unknown_intervention.intervention_id if unknown_intervention is not None else None
        ),
    }


def _request_body(
    client: TestClient, case: dict[str, Any], answer: str = "ABC-123"
) -> dict[str, str]:
    listed = client.get(
        "/api/interventions",
        params={"application_id": case["job"].application_id, "pending_only": "true"},
    )
    assert listed.status_code == 200, listed.text
    field_intervention = next(
        item
        for item in listed.json()["interventions"]
        if item["intervention_id"] == case["intervention_id"]
    )
    return {
        "answer": answer,
        "revision": field_intervention["revision"],
        "snapshot_hash": field_intervention["snapshot_hash"],
    }


def _assert_unmodified_after_rejection(
    session_factory: Any,
    case: dict[str, Any],
) -> None:
    from universal_auto_applier.interventions.store import get_intervention

    with session_scope(session_factory) as session:
        job = get_application_job(session, case["job"].application_id)
        intervention = get_intervention(session, case["intervention_id"])
        approval = get_latest_approval(session, case["job"].application_id)

    assert job is not None
    assert str(job.status) == ApplicationStatus.NEEDS_USER_INPUT.value
    assert _CORRECTIONS_KEY not in job.metadata
    assert intervention is not None
    assert str(intervention.status) == "pending"
    assert approval is not None
    assert approval.revoked_at is None


def test_correction_persists_provenance_revokes_only_its_approval_and_survives_reimport(
    tmp_path: Path,
) -> None:
    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(session_factory, "correction-success")
        other = _seed_case(session_factory, "correction-other-job")
        body = _request_body(client, case, answer="  ABC-123  ")

        response = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["status"] == "resume_queued"
        assert receipt["resume_status"] == ApplicationStatus.QUEUED.value
        assert receipt["pending_intervention_count"] == 0

        with session_scope(session_factory) as session:
            corrected = get_application_job(session, case["job"].application_id)
            target_approval = get_latest_approval(session, case["job"].application_id)
            other_approval = get_active_approval(session, other["job"].application_id)
            from universal_auto_applier.interventions.store import get_intervention

            intervention = get_intervention(session, case["intervention_id"])

        assert corrected is not None
        assert str(corrected.status) == ApplicationStatus.QUEUED.value
        corrections = corrected.metadata[_CORRECTIONS_KEY]
        assert len(corrections) == 1
        correction = next(iter(corrections.values()))
        assert correction["application_id"] == case["job"].application_id
        assert correction["intervention_id"] == case["intervention_id"]
        assert correction["answer"] == "ABC-123"
        assert correction["source"] == "owner_supplied"
        assert correction["revision"] == body["revision"]
        assert correction["snapshot_hash"] == case["snapshot"].snapshot_hash
        assert correction["saved_at"]
        assert correction["field_identity"] == {
            "field_token": "field-token-correction-success",
            "source_field_token": "source-token-correction-success",
            "step_identity": "step-correction-success",
            "form_progress_fingerprint": case["snapshot"].form_progress_fingerprint,
        }
        assert correction["receipt"] == receipt
        assert target_approval is not None and target_approval.revoked_at is not None
        assert other_approval is not None and other_approval.revoked_at is None
        assert intervention is not None and str(intervention.status) == "edited"

        # Queue producer metadata cannot inject or replace UAA-owned corrections.
        producer_reimport = case["job"].model_copy(
            update={
                "metadata": {
                    "producer_marker": "updated-by-reimport",
                    _CORRECTIONS_KEY: {"producer-injection": {"answer": "forged"}},
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, producer_reimport)
            reloaded = get_application_job(session, case["job"].application_id)

        assert reloaded is not None
        assert reloaded.metadata["producer_marker"] == "updated-by-reimport"
        assert reloaded.metadata[_CORRECTIONS_KEY] == corrections

        injected_job = _make_job("correction-fresh-injection")
        injected_job = injected_job.model_copy(
            update={
                "metadata": {
                    **injected_job.metadata,
                    _CORRECTIONS_KEY: {"producer-injection": {"answer": "forged"}},
                }
            }
        )
        with session_scope(session_factory) as session:
            upsert_application_job(session, injected_job)
            inserted = get_application_job(session, injected_job.application_id)
        assert inserted is not None
        assert _CORRECTIONS_KEY not in inserted.metadata


@pytest.mark.parametrize("stale_part", ["revision", "snapshot_hash"])
def test_stale_correction_is_rejected_without_changes(
    tmp_path: Path,
    stale_part: str,
) -> None:
    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(session_factory, f"correction-stale-{stale_part}")
        body = _request_body(client, case)
        body[stale_part] = "f" * 64

        response = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )

        assert response.status_code == 409, response.text
        _assert_unmodified_after_rejection(session_factory, case)


def test_same_correction_replay_does_not_requeue_and_conflicting_replay_is_rejected(
    tmp_path: Path,
) -> None:
    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(session_factory, "correction-replay")
        body = _request_body(client, case)
        first = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )
        assert first.status_code == 200, first.text

        # Simulate the worker advancing this job through preparation to review.
        with session_scope(session_factory) as session:
            update_application_status(
                session,
                case["job"].application_id,
                ApplicationStatus.IN_PROGRESS,
            )
            update_application_status(
                session,
                case["job"].application_id,
                ApplicationStatus.REVIEW_READY,
            )

        replay = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()

        conflict = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json={**body, "answer": "A different value"},
        )
        assert conflict.status_code == 409, conflict.text

        with session_scope(session_factory) as session:
            job = get_application_job(session, case["job"].application_id)
            approval = get_latest_approval(session, case["job"].application_id)

        assert job is not None
        assert str(job.status) == ApplicationStatus.REVIEW_READY.value
        assert len(job.metadata[_CORRECTIONS_KEY]) == 1
        assert approval is not None and approval.revoked_at is not None


def test_revoke_failure_rolls_back_intervention_correction_and_queue_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_auto_applier.submission.store as submission_store

    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(session_factory, "correction-rollback")
        body = _request_body(client, case)

        original_revoke = submission_store.revoke_approval

        def fail_revoke(session: Any, approval_id: str) -> None:
            original_revoke(session, approval_id)
            raise ValueError("simulated approval revoke failure")

        monkeypatch.setattr(submission_store, "revoke_approval", fail_revoke)
        response = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )

        assert response.status_code == 409, response.text
        _assert_unmodified_after_rejection(session_factory, case)


def test_active_pipeline_rejects_correction_without_mutating_state(tmp_path: Path) -> None:
    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(session_factory, "correction-active-run")
        body = _request_body(client, case)
        with session_scope(session_factory) as session:
            create_pipeline_run(session, run_id="active-correction-run", status="running")

        response = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )

        assert response.status_code == 409, response.text
        _assert_unmodified_after_rejection(session_factory, case)


def test_unknown_http_intervention_rejects_correction_without_mutating_state(
    tmp_path: Path,
) -> None:
    with _api(tmp_path) as (client, session_factory):
        case = _seed_case(
            session_factory,
            "correction-http-blocker",
            with_unknown_http_blocker=True,
        )
        body = _request_body(client, case)

        response = client.post(
            f"/api/interventions/{case['intervention_id']}/correct-and-resume",
            json=body,
        )

        assert response.status_code == 409, response.text
        assert "reconciliation first" in response.json()["detail"]
        _assert_unmodified_after_rejection(session_factory, case)

        from universal_auto_applier.interventions.store import get_intervention

        with session_scope(session_factory) as session:
            blocker = get_intervention(session, case["unknown_intervention_id"])
        assert blocker is not None and str(blocker.status) == "pending"


def test_correction_mapper_requires_matching_step_identity() -> None:
    application_id = "a" * 64
    source_field_token = "stable-source-token"
    correction = {
        "application_id": application_id,
        "answer": "Owner value",
        "saved_at": "2026-09-26T00:00:00+00:00",
        "field_identity": {
            "source_field_token": source_field_token,
            "step_identity": "step-expected",
        },
    }
    field = FormField(
        selector=source_field_token,
        name="reference_code",
        label="Reference code",
        type="text",
        required=True,
    )
    job = _make_job("correction-mapper-scope")
    correction["application_id"] = job.application_id
    job = job.model_copy(
        update={
            "metadata": {
                _CORRECTIONS_KEY: {"saved-correction": correction},
                "_uaa_current_form_identity": {"step_identity": "step-other"},
            }
        }
    )

    wrong_step = map_field(field, CandidateProfile(), job)
    assert wrong_step is None
    assert job.metadata[_CORRECTIONS_KEY]["saved-correction"]["answer"] == "Owner value"

    same_token_expected_step = job.model_copy(
        update={
            "metadata": {
                **job.metadata,
                "_uaa_current_form_identity": {"step_identity": "step-expected"},
            }
        }
    )
    matched = map_field(field, CandidateProfile(), same_token_expected_step)
    assert matched is not None
    assert matched.value == "Owner value"
    assert matched.source == "user_input"
    assert same_token_expected_step.metadata[_CORRECTIONS_KEY]["saved-correction"]["answer"] == (
        "Owner value"
    )
