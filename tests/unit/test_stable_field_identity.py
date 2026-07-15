"""Unit tests for stable field identity and field-record consolidation.

These tests exercise :func:`compute_field_token` and
:func:`consolidate_fields` directly, without a browser. They prove the
properties required by the stable-field-identity workpackage:

- Same field keeps the same token after fields are inserted, removed,
  hidden, or revealed (tokens contain NO extraction index).
- Radio options in one group share one group token.
- Distinct fields with similar labels remain distinct.
- Two similar radio groups remain distinct.
- Iframes remain distinguishable from main-page fields.
- Dynamic page URLs (query strings, fragments) do not change top-frame
  field identity.
- Iframe URLs with volatile query strings do not change iframe field
  identity.
- Consolidation: later ``filled`` supersedes earlier
  ``intervention_needed``; one terminal record per logical field.
"""

from __future__ import annotations

from typing import Any

from universal_auto_applier.browser.live_models import LiveFieldRecord
from universal_auto_applier.form_engine.live_executor import (
    compute_field_token,
    consolidate_fields,
)

# ---------------------------------------------------------------------------
# Token stability: no extraction index
# ---------------------------------------------------------------------------


class TestTokenHasNoExtractionIndex:
    def test_token_starts_with_lf_prefix(self) -> None:
        """Tokens use the 'lf-' prefix, not the legacy 'live-field-' prefix."""
        token = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="first_name",
            name="first_name",
            label="First name",
        )
        assert token.startswith("lf-"), f"Expected 'lf-' prefix, got {token!r}"

    def test_token_contains_no_index(self) -> None:
        """The token must not contain any positional index like '0-1'."""
        token = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="email",
            name="email",
            label="Email address",
        )
        # The hash portion is 12 hex chars after 'lf-'.
        assert len(token) == 15, f"Expected 'lf-' + 12 hex chars, got {token!r}"
        assert token[3:].isalnum(), f"Token hash must be alphanumeric: {token!r}"

    def test_same_dom_properties_produce_same_token(self) -> None:
        """Same id/name/label/type/frame -> same token, every time."""
        kwargs: dict[str, Any] = dict(
            frame_id="main",
            field_type="text",
            element_id="phone",
            name="phone",
            label="Phone number",
        )
        t1 = compute_field_token(**kwargs)
        t2 = compute_field_token(**kwargs)
        assert t1 == t2

    def test_token_independent_of_call_order(self) -> None:
        """Calling compute_field_token for field A then B then A again
        produces the same token for A both times. There is no hidden
        state or counter."""
        token_a_1 = compute_field_token(
            frame_id="main", field_type="text", element_id="a", name="a", label="A"
        )
        _ = compute_field_token(
            frame_id="main", field_type="text", element_id="b", name="b", label="B"
        )
        token_a_2 = compute_field_token(
            frame_id="main", field_type="text", element_id="a", name="a", label="A"
        )
        assert token_a_1 == token_a_2


# ---------------------------------------------------------------------------
# DOM insert stability: inserting a field above does not change token
# ---------------------------------------------------------------------------


class TestDomInsertStability:
    def test_phone_token_unchanged_when_middle_name_inserted_above(self) -> None:
        """The real-ATS reproduction: a field (phone) is at some index.
        A new field (middle_name) is inserted above it. The phone field's
        token must NOT change, because the token is derived from DOM
        properties (id, name, label), not from the extraction index."""
        phone_token_before = compute_field_token(
            frame_id="main",
            field_type="phone",
            element_id="phone",
            name="phone",
            label="Phone number",
        )
        # After inserting middle_name above phone, the phone field's
        # extraction index would change (e.g. from 2 to 3). But its
        # DOM properties (id, name, label, type) are unchanged.
        phone_token_after = compute_field_token(
            frame_id="main",
            field_type="phone",
            element_id="phone",
            name="phone",
            label="Phone number",
        )
        assert phone_token_before == phone_token_after

    def test_inserted_field_gets_distinct_token(self) -> None:
        """The newly inserted middle_name field gets its own token,
        distinct from phone."""
        phone_token = compute_field_token(
            frame_id="main",
            field_type="phone",
            element_id="phone",
            name="phone",
            label="Phone number",
        )
        middle_token = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="middle_name",
            name="middle_name",
            label="Middle name",
        )
        assert phone_token != middle_token


# ---------------------------------------------------------------------------
# Radio group identity: one token per group
# ---------------------------------------------------------------------------


class TestRadioGroupIdentity:
    def test_all_options_in_group_share_token(self) -> None:
        """A radio group's token is the same regardless of which option
        the iterator reached first. The token is derived from
        (frame_id, 'radio', name, question_label), not from the option's
        value or position."""
        group_token = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="k8s_exp",
            label="Do you have experience with Kubernetes?",
            is_radio_group=True,
        )
        # Computing again with the same group properties -> same token.
        group_token_2 = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="k8s_exp",
            label="Do you have experience with Kubernetes?",
            is_radio_group=True,
        )
        assert group_token == group_token_2

    def test_two_similar_radio_groups_are_distinct(self) -> None:
        """Two radio groups with Yes/No options but different question
        labels and different name attributes must have DISTINCT tokens."""
        k8s_token = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="k8s_exp",
            label="Do you have experience with Kubernetes?",
            is_radio_group=True,
        )
        docker_token = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="docker_exp",
            label="Do you have experience with Docker?",
            is_radio_group=True,
        )
        assert k8s_token != docker_token, (
            "Two radio groups with different names and questions must have distinct tokens"
        )

    def test_radio_groups_with_same_name_but_different_questions_are_distinct(
        self,
    ) -> None:
        """Edge case: two radio groups that share the same `name`
        attribute (rare but possible in SPA frameworks) but ask different
        questions must have distinct tokens (the question label
        disambiguates)."""
        token_a = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="question",
            label="Do you have experience with Kubernetes?",
            is_radio_group=True,
        )
        token_b = compute_field_token(
            frame_id="main",
            field_type="radio",
            element_id="",
            name="question",
            label="Do you have experience with Docker?",
            is_radio_group=True,
        )
        assert token_a != token_b, (
            "Radio groups with the same name but different questions must be distinct"
        )


# ---------------------------------------------------------------------------
# Iframe identity: distinct from main-page fields
# ---------------------------------------------------------------------------


class TestIframeIdentity:
    def test_main_frame_and_iframe_with_same_field_are_distinct(self) -> None:
        """A field with id='first_name' in the main frame and a field
        with id='first_name' in an iframe must have DIFFERENT tokens."""
        main_token = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="first_name",
            name="first_name",
            label="First name",
        )
        iframe_token = compute_field_token(
            frame_id="https://boards.greenhouse.io/embed/job_app/123",
            field_type="text",
            element_id="first_name",
            name="first_name",
            label="First name",
        )
        assert main_token != iframe_token, (
            "Main-frame and iframe fields with the same id must have different tokens"
        )

    def test_iframe_url_query_string_does_not_change_token(self) -> None:
        """An iframe URL with a query string (e.g. ?token=xyz) and the
        same URL without the query string must produce the same token,
        because query strings are volatile (session tokens, timestamps)."""
        from universal_auto_applier.form_engine.live_executor import _frame_identity

        iframe_id_1 = _frame_identity(
            "https://boards.greenhouse.io/embed/job_app/123?token=abc123",
            is_main_frame=False,
        )
        iframe_id_2 = _frame_identity(
            "https://boards.greenhouse.io/embed/job_app/123?token=def456",
            is_main_frame=False,
        )
        assert iframe_id_1 == iframe_id_2, (
            "Iframe URLs differing only by query string must map to the same frame identity"
        )
        # The frame identity should be the URL without query/fragment.
        assert iframe_id_1 == "https://boards.greenhouse.io/embed/job_app/123"

    def test_iframe_url_fragment_does_not_change_token(self) -> None:
        """An iframe URL with a fragment (#section) and the same URL
        without it must produce the same frame identity."""
        from universal_auto_applier.form_engine.live_executor import _frame_identity

        iframe_id_1 = _frame_identity(
            "https://boards.greenhouse.io/embed/job_app/123#section1",
            is_main_frame=False,
        )
        iframe_id_2 = _frame_identity(
            "https://boards.greenhouse.io/embed/job_app/123",
            is_main_frame=False,
        )
        assert iframe_id_1 == iframe_id_2

    def test_main_frame_always_returns_main_sentinel(self) -> None:
        """The main frame always returns 'main' regardless of its URL,
        so dynamic page URLs do not change top-frame field identity."""
        from universal_auto_applier.form_engine.live_executor import _frame_identity

        assert _frame_identity("https://example.com/job/123", is_main_frame=True) == "main"
        assert (
            _frame_identity("https://example.com/job/123?tab=apply", is_main_frame=True) == "main"
        )
        assert _frame_identity("https://different.com/page", is_main_frame=True) == "main"


# ---------------------------------------------------------------------------
# Distinct fields with similar labels
# ---------------------------------------------------------------------------


class TestDistinctFieldsWithSimilarLabels:
    def test_different_ids_same_label_are_distinct(self) -> None:
        """Two text fields with the same label but different ids must
        have distinct tokens."""
        t1 = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="email1",
            name="email1",
            label="Email",
        )
        t2 = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="email2",
            name="email2",
            label="Email",
        )
        assert t1 != t2

    def test_same_id_different_types_are_distinct(self) -> None:
        """A text field and a number field with the same id (unusual but
        possible) must have distinct tokens because the type is part of
        the canonical identity."""
        t1 = compute_field_token(
            frame_id="main",
            field_type="text",
            element_id="field1",
            name="field1",
            label="Field",
        )
        t2 = compute_field_token(
            frame_id="main",
            field_type="number",
            element_id="field1",
            name="field1",
            label="Field",
        )
        assert t1 != t2


# ---------------------------------------------------------------------------
# Consolidation: one terminal record per logical field
# ---------------------------------------------------------------------------


def _record(
    token: str,
    status: str,
    label: str = "Q?",
    source: str | None = None,
) -> LiveFieldRecord:
    """Build a LiveFieldRecord with the given token and status.

    Uses model_validate to bypass the Literal type check on status,
    allowing test code to pass any status string without duplicating
    the Literal definition.
    """
    data: dict[str, Any] = {
        "page_url": "https://example.com",
        "selector": "input[name='q']",
        "label": label,
        "field_type": "text",
        "status": status,
        "field_token": token,
        "source": source,
    }
    return LiveFieldRecord.model_validate(data)


class TestConsolidation:
    def test_filled_supersedes_intervention_needed(self) -> None:
        """The critical real-ATS fix: a field first seen as
        intervention_needed and later filled must end up as ONE
        filled record in the final report."""
        rec1 = _record("lf-aaa", "intervention_needed")
        rec2 = _record("lf-aaa", "filled", source="llm_grounded")
        result = consolidate_fields([rec1, rec2])
        assert len(result) == 1, f"Expected 1 record, got {len(result)}"
        assert result[0].status == "filled"
        assert result[0].source == "llm_grounded"

    def test_intervention_needed_does_not_supersede_filled(self) -> None:
        """If a field was filled and then re-observed as
        intervention_needed (e.g. re-extraction without the fill), the
        earlier filled status must be preserved (filled is more terminal
        than intervention_needed)."""
        rec1 = _record("lf-aaa", "filled", source="llm_grounded")
        rec2 = _record("lf-aaa", "intervention_needed")
        result = consolidate_fields([rec1, rec2])
        assert len(result) == 1
        assert result[0].status == "filled", (
            "Earlier filled must NOT be superseded by later intervention_needed"
        )

    def test_multiple_distinct_fields_preserved(self) -> None:
        """Three distinct fields (different tokens) produce three records."""
        recs = [
            _record("lf-a", "filled"),
            _record("lf-b", "intervention_needed"),
            _record("lf-c", "skipped"),
        ]
        result = consolidate_fields(recs)
        assert len(result) == 3

    def test_duplicate_unresolved_collapses_to_one(self) -> None:
        """Two intervention_needed records for the same token collapse
        to one."""
        recs = [
            _record("lf-a", "intervention_needed"),
            _record("lf-a", "intervention_needed"),
        ]
        result = consolidate_fields(recs)
        assert len(result) == 1
        assert result[0].status == "intervention_needed"

    def test_no_token_records_pass_through(self) -> None:
        """Records without a field_token (legacy/edge case) are passed
        through unchanged -- they cannot be consolidated by identity."""
        recs = [
            LiveFieldRecord(
                page_url="https://example.com",
                selector="input",
                label="Q",
                field_type="text",
                status="filled",
                field_token="",
            ),
            LiveFieldRecord(
                page_url="https://example.com",
                selector="input2",
                label="Q2",
                field_type="text",
                status="skipped",
                field_token="",
            ),
        ]
        result = consolidate_fields(recs)
        assert len(result) == 2

    def test_empty_list_returns_empty(self) -> None:
        assert consolidate_fields([]) == []

    def test_order_preserved_by_first_appearance(self) -> None:
        """The consolidated list preserves the order of first appearance
        (DOM order from the initial observation). The later same-priority
        record wins the content update but keeps the position."""
        recs = [
            _record("lf-c", "filled", label="C"),
            _record("lf-a", "filled", label="A"),
            _record("lf-b", "filled", label="B"),
            _record("lf-a", "filled", label="A-updated"),
        ]
        result = consolidate_fields(recs)
        assert len(result) == 3
        # Order is by first appearance: C, A, B.
        assert result[0].label == "C"
        assert result[1].label == "A-updated"  # Position kept, content updated.
        assert result[2].label == "B"

    def test_filled_supersedes_failed(self) -> None:
        """A later filled record supersedes an earlier failed record."""
        recs = [_record("lf-x", "failed"), _record("lf-x", "filled")]
        result = consolidate_fields(recs)
        assert len(result) == 1
        assert result[0].status == "filled"

    def test_intervention_needed_supersedes_skipped(self) -> None:
        """A later intervention_needed record supersedes an earlier
        skipped record (the field became required-unresolved)."""
        recs = [_record("lf-x", "skipped"), _record("lf-x", "intervention_needed")]
        result = consolidate_fields(recs)
        assert len(result) == 1
        assert result[0].status == "intervention_needed"
