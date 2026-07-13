"""Tests for :mod:`universal_auto_applier.interventions.answer_memory`.

Covers normalization, store, retrieve, edit, delete, and use-count tracking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_auto_applier.interventions.answer_memory import (
    delete_answer,
    list_answers,
    normalize_question,
    retrieve_answer,
    store_answer,
    update_answer,
)
from universal_auto_applier.persistence.db import make_session_factory
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def session_factory(tmp_path: Path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    db_path = tmp_path / "test_memory.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


class TestNormalizeQuestion:
    def test_lowercases(self) -> None:
        assert (
            normalize_question("Do You Require Visa Sponsorship?")
            == "do you require visa sponsorship"
        )

    def test_strips_whitespace(self) -> None:
        assert normalize_question("  What is your name?  ") == "what is your name"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_question("What   is   your   name?") == "what is your name"

    def test_removes_trailing_punctuation(self) -> None:
        assert normalize_question("Are you eligible?") == "are you eligible"
        assert normalize_question("Please answer:") == "please answer"

    def test_removes_leading_articles(self) -> None:
        assert normalize_question("The salary expectation") == "salary expectation"
        assert normalize_question("A question") == "question"

    def test_idempotent(self) -> None:
        q = "Do you require visa sponsorship?"
        assert normalize_question(q) == normalize_question(normalize_question(q))


class TestStoreAnswer:
    def test_store_new_answer(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = store_answer(
                session,
                question="Do you require visa sponsorship?",
                answer="No",
            )

        assert row.normalized_question == "do you require visa sponsorship"
        assert row.answer == "No"
        assert row.source == "user_confirmed"
        assert row.confidence == 1.0

    def test_store_updates_existing(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="Yes")
            row = store_answer(session, question="Sponsorship?", answer="No")

        assert row.answer == "No"

    def test_store_with_different_source(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            row = store_answer(
                session,
                question="Experience?",
                answer="5 years",
                source="profile_derived",
                confidence=0.8,
            )

        assert row.source == "profile_derived"
        assert row.confidence == 0.8


class TestRetrieveAnswer:
    def test_retrieve_existing(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Do you require visa sponsorship?", answer="No")
            # Retrieval normalizes the question — same text should match.
            memory = retrieve_answer(session, "Do you require visa sponsorship?")

        assert memory is not None
        assert memory.answer == "No"

    def test_retrieve_nonexistent(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            result = retrieve_answer(session, "Nonexistent question?")

        assert result is None

    def test_retrieve_updates_use_count(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="No")
            retrieve_answer(session, "Sponsorship?")
            retrieve_answer(session, "Sponsorship?")
            memory = retrieve_answer(session, "Sponsorship?")

        assert memory is not None
        assert memory.use_count == 3
        assert memory.last_used is not None


class TestUpdateAnswer:
    def test_update_existing(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="Yes")
            row = update_answer(session, "Sponsorship?", "No")

        assert row is not None
        assert row.answer == "No"

    def test_update_nonexistent(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            result = update_answer(session, "Nonexistent?", "value")

        assert result is None


class TestDeleteAnswer:
    def test_delete_existing(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="No")
            deleted = delete_answer(session, "Sponsorship?")

        assert deleted is True

    def test_delete_nonexistent(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            deleted = delete_answer(session, "Nonexistent?")

        assert deleted is False

    def test_deleted_answer_not_retrievable(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="No")
            delete_answer(session, "Sponsorship?")
            result = retrieve_answer(session, "Sponsorship?")

        assert result is None


class TestListAnswers:
    def test_list_empty(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            answers = list_answers(session)

        assert len(answers) == 0

    def test_list_multiple(self, session_factory) -> None:
        from universal_auto_applier.persistence.db import session_scope

        with session_scope(session_factory) as session:
            store_answer(session, question="Sponsorship?", answer="No")
            store_answer(session, question="Experience?", answer="5 years")
            answers = list_answers(session)

        assert len(answers) == 2
