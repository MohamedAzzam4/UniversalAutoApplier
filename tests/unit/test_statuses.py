"""Unit tests for the :mod:`universal_auto_applier.core.statuses` enums.

Confirms the finite sets documented in ``DATA_CONTRACTS.md`` are present and
that the lifecycle transitions table covers every status exactly once.
"""

from __future__ import annotations

from universal_auto_applier.core.statuses import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ApplicationStatus,
)


def test_application_status_matches_contract() -> None:
    expected = {
        "discovered",
        "evaluated",
        "rejected",
        "tailored",
        "ready_to_apply",
        "queued",
        "in_progress",
        "needs_user_input",
        "review_ready",
        "submitted",
        "needs_review",
        "applied",
        "failed",
        "skipped",
        "closed",
        "blocked",
    }
    actual = {status.value for status in ApplicationStatus}
    assert actual == expected


def test_terminal_statuses_match_contract() -> None:
    expected = {"applied", "rejected", "skipped", "closed"}
    actual = {status.value for status in TERMINAL_STATUSES}
    assert actual == expected


def test_allowed_transitions_cover_every_status() -> None:
    # Every status must appear as a key in ALLOWED_TRANSITIONS exactly once.
    assert set(ALLOWED_TRANSITIONS.keys()) == set(ApplicationStatus)


def test_allowed_transitions_match_contract() -> None:
    # Spot-check the most safety-critical transitions.
    # review_ready is the only allowed entry to submitted.
    assert ALLOWED_TRANSITIONS[ApplicationStatus.REVIEW_READY] == frozenset(
        {ApplicationStatus.SUBMITTED}
    )
    # submitted -> applied OR needs_review (never back to queued directly).
    assert ALLOWED_TRANSITIONS[ApplicationStatus.SUBMITTED] == frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.NEEDS_REVIEW}
    )
    # applied is terminal.
    assert ALLOWED_TRANSITIONS[ApplicationStatus.APPLIED] == frozenset()


def test_review_ready_only_transitions_to_submitted() -> None:
    """Safety invariant: the only way to submit is through review_ready."""
    for status, targets in ALLOWED_TRANSITIONS.items():
        if status == ApplicationStatus.REVIEW_READY:
            continue
        assert ApplicationStatus.SUBMITTED not in targets, (
            f"{status} can transition directly to submitted, which bypasses review"
        )


def test_terminal_statuses_have_no_outgoing_transitions() -> None:
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset(), status
