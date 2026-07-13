"""Answer memory store — store and retrieve user-confirmed answers.

Per ``ROADMAP.md`` WP 5.2, answer memory stores confirmed answers by
normalized question pattern. Memory is used only after exact or
high-confidence semantic match.

Rules from DATA_CONTRACTS.md:
- Do not store answers from AI unless user approved them.
- Do not apply answer memory to semantically different questions.
- User must be able to edit or delete memory entries.

The store uses SQLAlchemy for persistence.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_auto_applier.core.models import AnswerMemory
from universal_auto_applier.persistence.models import AnswerMemoryRow

logger = logging.getLogger("universal_auto_applier.interventions.answer_memory")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_question(question: str) -> str:
    """Normalize a question string for storage and matching.

    Normalization:
    - Lowercase
    - Strip leading/trailing whitespace
    - Collapse internal whitespace to single spaces
    - Remove trailing punctuation (?, ., !, :)
    - Remove common articles (a, an, the) at the start
    """
    s = question.strip().lower()
    # Collapse whitespace.
    s = re.sub(r"\s+", " ", s)
    # Remove trailing punctuation.
    s = re.sub(r"[?.!:;]+$", "", s).strip()
    # Remove leading articles.
    s = re.sub(r"^(a|an|the)\s+", "", s)
    return s


def _row_to_memory(row: AnswerMemoryRow) -> AnswerMemory:
    return AnswerMemory(
        normalized_question=row.normalized_question,
        answer=row.answer,
        source=row.source,
        confidence=row.confidence,
        last_used=row.last_used,
        use_count=row.use_count,
    )


def store_answer(
    session: Session,
    *,
    question: str,
    answer: str,
    source: str = "user_confirmed",
    confidence: float = 1.0,
) -> AnswerMemoryRow:
    """Store or update an answer in memory.

    If an answer for the normalized question already exists, it is updated.
    Otherwise, a new entry is created.

    Args:
        session: An open SQLAlchemy session.
        question: The original question text.
        answer: The confirmed answer.
        source: The source of the answer (user_confirmed, profile_derived, adapter_default).
        confidence: Confidence level (0.0-1.0).

    Returns:
        The stored :class:`AnswerMemoryRow`.
    """
    normalized = normalize_question(question)

    stmt = select(AnswerMemoryRow).where(AnswerMemoryRow.normalized_question == normalized)
    existing = session.execute(stmt).scalars().first()

    if existing is not None:
        existing.answer = answer
        existing.source = source
        existing.confidence = confidence
        existing.last_used = _utcnow()
        session.flush()
        logger.info("answer memory updated: question=%s answer=%s", normalized[:40], answer)
        return existing

    row = AnswerMemoryRow(
        normalized_question=normalized,
        answer=answer,
        source=source,
        confidence=confidence,
        last_used=None,
        use_count=0,
    )
    session.add(row)
    session.flush()
    logger.info("answer memory created: question=%s answer=%s", normalized[:40], answer)
    return row


def retrieve_answer(
    session: Session,
    question: str,
) -> AnswerMemory | None:
    """Retrieve a stored answer for a question.

    The question is normalized before lookup. Returns None if no match.

    Note: This performs exact match on the normalized question. Semantic
    matching is deferred to a future phase (requires AI).
    """
    normalized = normalize_question(question)
    stmt = select(AnswerMemoryRow).where(AnswerMemoryRow.normalized_question == normalized)
    row = session.execute(stmt).scalars().first()
    if row is None:
        return None

    # Update last_used and use_count.
    row.last_used = _utcnow()
    row.use_count += 1
    session.flush()

    return _row_to_memory(row)


def list_answers(session: Session) -> list[AnswerMemory]:
    """List all stored answers."""
    stmt = select(AnswerMemoryRow).order_by(AnswerMemoryRow.normalized_question)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_memory(row) for row in rows]


def delete_answer(
    session: Session,
    question: str,
) -> bool:
    """Delete a stored answer by question.

    Returns True if an answer was deleted, False if no match was found.
    """
    normalized = normalize_question(question)
    stmt = select(AnswerMemoryRow).where(AnswerMemoryRow.normalized_question == normalized)
    row = session.execute(stmt).scalars().first()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    logger.info("answer memory deleted: question=%s", normalized[:40])
    return True


def update_answer(
    session: Session,
    question: str,
    new_answer: str,
) -> AnswerMemoryRow | None:
    """Update an existing stored answer.

    Returns the updated row, or None if no match was found.
    """
    normalized = normalize_question(question)
    stmt = select(AnswerMemoryRow).where(AnswerMemoryRow.normalized_question == normalized)
    row = session.execute(stmt).scalars().first()
    if row is None:
        return None
    row.answer = new_answer
    row.last_used = _utcnow()
    session.flush()
    logger.info("answer memory updated: question=%s new_answer=%s", normalized[:40], new_answer)
    return row


__all__ = [
    "normalize_question",
    "store_answer",
    "retrieve_answer",
    "list_answers",
    "delete_answer",
    "update_answer",
]
