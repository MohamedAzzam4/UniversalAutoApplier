"""Candidate truth ledger — normalized candidate-fact service.

Per the llm-question-resolution workpackage, the truth ledger is the
single source of truth for candidate facts used in LLM grounding. It
combines:

- Candidate profile (from ``ApplicationJob.metadata.candidate_profile``)
- Original CV markdown (from ``ApplicationJob.documents.cv_md``)
- Tailored CV markdown (from ``ApplicationJob.documents.cv_md`` —
  JobHunter overwrites this with the tailored version)
- Tailored cover letter markdown (from ``ApplicationJob.documents.cover_letter_md``)
- Job description and evaluation (from ``ApplicationJob.job_description``
  and ``ApplicationJob.evaluation_reason``)
- Explicit application metadata (from ``ApplicationJob.metadata`` keys
  like ``question_answers``, ``application_answers``, ``form_answers``)
- Previously approved reusable answers (from the ``answer_memories``
  table via the existing :mod:`interventions.answer_memory` module)

Every fact has provenance. Explicit user facts override inferred facts.
Contradictory facts produce an :class:`Intervention` (handled by the
caller; this module returns the contradiction for the caller to act on).

The ledger is read-only during question resolution. It does not mutate
the candidate profile or documents. Reusable approved answers are
written via the existing :mod:`interventions.answer_memory` module
(``remember_answer`` flow), not here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_auto_applier.core.models import ApplicationJob, CandidateProfile

logger = logging.getLogger("universal_auto_applier.llm.truth_ledger")


@dataclass(frozen=True)
class Fact:
    """One candidate fact with provenance.

    Attributes:
        fact: The factual statement (e.g. "Candidate has 3 years of Python experience").
        source: Where the fact came from. One of:
            ``candidate_profile``, ``cv_markdown``, ``cover_letter_markdown``,
            ``job_description``, ``answer_memory``, ``application_metadata``,
            ``job_evaluation``.
        source_ref: Optional reference into the source (e.g. field name,
            line number, or question text).
        confidence: How reliable this fact is (0.0–1.0). Explicit user
            facts are 1.0; inferred facts are lower.
    """

    fact: str
    source: str
    source_ref: str = ""
    confidence: float = 1.0


@dataclass
class CandidateTruthLedger:
    """A read-only collection of candidate facts with provenance.

    The ledger is built from an :class:`ApplicationJob` and its
    resolved :class:`CandidateProfile`. It does NOT mutate any input.
    """

    facts: list[Fact] = field(default_factory=list[Fact])
    # Contradictions detected during build. The caller may create an
    # intervention for each contradiction.
    contradictions: list[tuple[Fact, Fact]] = field(default_factory=list[tuple[Fact, Fact]])

    def facts_for_subject(self, subject: str) -> list[Fact]:
        """Return facts whose text contains the given subject.

        The subject is normalized (lowercase, alphanumeric only) before
        matching. This is a simple substring match, not semantic search.
        """
        normalized_subject = _normalize_text(subject)
        if not normalized_subject:
            return []
        return [fact for fact in self.facts if normalized_subject in _normalize_text(fact.fact)]

    def has_explicit_fact_for(self, question_text: str) -> Fact | None:
        """Check if the ledger has an explicit (user-provided) answer
        for the given question text.

        This checks ``application_metadata`` and ``answer_memory`` sources
        only (explicit user facts). Returns the matching fact or None.
        """
        normalized_q = _normalize_text(question_text)
        for fact in self.facts:
            if fact.source not in ("application_metadata", "answer_memory"):
                continue
            # For application_metadata, the source_ref is the question text.
            if _normalize_text(fact.source_ref) == normalized_q:
                return fact
            # Also check if the question text is contained in the fact.
            if normalized_q and normalized_q in _normalize_text(fact.fact):
                return fact
        return None

    def to_evidence_summary(self, max_facts: int = 20) -> str:
        """Return a concise text summary of the ledger for LLM grounding.

        This is the text sent to the LLM as grounding context. It
        includes only the fact text and source (not internal IDs or
        confidence scores).
        """
        lines: list[str] = []
        for fact in self.facts[:max_facts]:
            lines.append(f"[{fact.source}] {fact.fact}")
        return "\n".join(lines)


def _normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, alphanumeric + spaces only."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _read_document(path_str: str | None) -> str:
    """Read a markdown document from disk, returning empty string on failure."""
    if not path_str:
        return ""
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return ""
    if path.stat().st_size > 2_000_000:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def build_ledger(
    job: ApplicationJob,
    candidate: CandidateProfile,
    answer_memory_facts: list[Fact] | None = None,
) -> CandidateTruthLedger:
    """Build a :class:`CandidateTruthLedger` from a job and candidate.

    Args:
        job: The :class:`ApplicationJob` with metadata, documents, and
            job description.
        candidate: The resolved :class:`CandidateProfile`.
        answer_memory_facts: Optional list of reusable approved answers
            (from :mod:`interventions.answer_memory`). These are
            explicit user facts with ``source='answer_memory'``.

    Returns:
        A :class:`CandidateTruthLedger` with all facts and provenance.

    Fact priority (highest first):
    1. ``answer_memory`` (user-approved reusable answers)
    2. ``application_metadata`` (explicit per-job answers)
    3. ``candidate_profile`` (structured profile fields)
    4. ``cover_letter_markdown`` (tailored cover letter)
    5. ``cv_markdown`` (tailored CV)
    6. ``job_description`` / ``job_evaluation`` (context, not candidate facts)
    """
    ledger = CandidateTruthLedger()

    # 1. Answer memory (highest priority — user-approved reusable answers).
    if answer_memory_facts:
        for fact in answer_memory_facts:
            ledger.facts.append(fact)

    # 2. Application metadata (explicit per-job answers).
    metadata: dict[str, Any] = job.metadata or {}
    for key in ("question_answers", "application_answers", "form_answers"):
        raw = metadata.get(key)
        if not isinstance(raw, dict):
            continue
        # Narrow to dict[str, Any] for type safety.
        from typing import cast as _cast

        answers = _cast(dict[str, Any], raw)
        for question, answer in answers.items():
            if answer is None:
                continue
            ledger.facts.append(
                Fact(
                    fact=f"Answer to '{question}': {answer}",
                    source="application_metadata",
                    source_ref=str(question),
                    confidence=1.0,
                )
            )

    # 3. Candidate profile (structured fields).
    profile_snapshot_raw = metadata.get("candidate_profile")
    if isinstance(profile_snapshot_raw, dict):
        from typing import cast as _cast

        profile_snapshot = _cast(dict[str, Any], profile_snapshot_raw)
        for field_name, value in profile_snapshot.items():
            if value is None or value == "":
                continue
            ledger.facts.append(
                Fact(
                    fact=f"{field_name}: {value}",
                    source="candidate_profile",
                    source_ref=str(field_name),
                    confidence=1.0,
                )
            )
    else:
        profile_snapshot = {}
    # Also add facts from the resolved CandidateProfile (may have been
    # loaded from UAA_CANDIDATE_PROFILE).
    for field_name in (
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "linkedin_url",
        "github_url",
        "city",
        "country",
        "current_position",
        "work_authorization",
        "requires_sponsorship",
        "years_of_experience",
    ):
        value = getattr(candidate, field_name, None)
        if value is None or value == "":
            continue
        # Skip if already in profile_snapshot (avoid duplicates).
        if profile_snapshot.get(field_name) is not None:
            continue
        ledger.facts.append(
            Fact(
                fact=f"{field_name}: {value}",
                source="candidate_profile",
                source_ref=field_name,
                confidence=1.0,
            )
        )

    # 4. Cover letter markdown (tailored).
    if job.documents and job.documents.cover_letter_md:
        cover_text = _read_document(job.documents.cover_letter_md)
        if cover_text:
            # Store as a single fact (the LLM can extract specifics).
            ledger.facts.append(
                Fact(
                    fact=cover_text[:2000],  # Truncate to keep prompt small.
                    source="cover_letter_markdown",
                    source_ref=job.documents.cover_letter_md,
                    confidence=0.9,
                )
            )

    # 5. CV markdown (tailored).
    if job.documents and job.documents.cv_md:
        cv_text = _read_document(job.documents.cv_md)
        if cv_text:
            ledger.facts.append(
                Fact(
                    fact=cv_text[:2000],  # Truncate to keep prompt small.
                    source="cv_markdown",
                    source_ref=job.documents.cv_md,
                    confidence=0.9,
                )
            )

    # 6. Job description and evaluation (context, not candidate facts).
    if job.job_description:
        ledger.facts.append(
            Fact(
                fact=f"Job description: {job.job_description[:1500]}",
                source="job_description",
                source_ref="job_description",
                confidence=0.7,
            )
        )
    if job.evaluation_reason:
        ledger.facts.append(
            Fact(
                fact=f"Job evaluation: {job.evaluation_reason}",
                source="job_evaluation",
                source_ref="evaluation_reason",
                confidence=0.7,
            )
        )

    # Detect contradictions in explicit facts (same field, different values).
    _detect_contradictions(ledger)

    return ledger


def _detect_contradictions(ledger: CandidateTruthLedger) -> None:
    """Detect contradictory explicit facts and add them to the ledger.

    A contradiction is when two explicit facts (application_metadata or
    answer_memory) refer to the same question/field but have different
    values. The caller should create an intervention for each
    contradiction.
    """
    # Group explicit facts by normalized source_ref (question text).
    by_question: dict[str, list[Fact]] = {}
    for fact in ledger.facts:
        if fact.source not in ("application_metadata", "answer_memory"):
            continue
        key = _normalize_text(fact.source_ref)
        if not key:
            continue
        by_question.setdefault(key, []).append(fact)

    for _key, facts in by_question.items():
        if len(facts) < 2:
            continue
        # Check if any two facts have different values.
        seen_values: set[str] = set()
        for fact in facts:
            # Extract the answer part (after the colon).
            answer_part = fact.fact.split(":", 1)[-1].strip().lower()
            seen_values.add(answer_part)
        if len(seen_values) > 1:
            # Contradiction found.
            for i, fact in enumerate(facts):
                for other in facts[i + 1 :]:
                    if (
                        fact.fact.split(":", 1)[-1].strip().lower()
                        != other.fact.split(":", 1)[-1].strip().lower()
                    ):
                        ledger.contradictions.append((fact, other))


__all__ = [
    "CandidateTruthLedger",
    "Fact",
    "build_ledger",
]
