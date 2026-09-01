"""Supervisor policy engine — deterministic gates AROUND the LLM.

The LLM never decides its own permissions. Every planner decision passes
through these deterministic classification gates:

- **Class A — automatically resolvable** when an exact trusted source
  exists (owner policy, exact answer memory, explicit job-specific owner
  answer, exact candidate fact). The answer must be traceable to a source.
- **Class B — human required** (CAPTCHA, 2FA, login, unknown salary, legal
  declarations, sensitive consent, anything where fabrication would be
  required). Always hands off.
- **Class C — likely software defect** (e.g. the candidate fact clearly
  exists but the mapper reports unresolved) → repair ticket, never code
  changes while an attempt runs.
- **Class D — hard blocker** → stop that application, no retry loop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from universal_auto_applier.interventions.answer_memory import normalize_question
from universal_auto_applier.supervisor.models import (
    AnswerSource,
    InterventionView,
    OwnerPolicy,
    ReasonCode,
    SupervisorAction,
    SupervisorDecision,
)

DecisionClass = Literal["A", "B", "C", "D"]


@dataclass(frozen=True)
class PolicyClassification:
    """Deterministic classification of one pending intervention."""

    decision_class: DecisionClass
    reason_code: ReasonCode
    answer: str | None = None
    answer_source: AnswerSource | None = None
    policy_id: str | None = None

    @property
    def auto_resolvable(self) -> bool:
        return (
            self.decision_class == "A"
            and self.answer is not None
            and self.answer_source is not None
        )


# Keyword heuristics for high-risk categories. The LLM question classifier
# may not be configured; these are conservative deterministic fallbacks —
# when in doubt, the question is treated as high risk (Class B).
_HIGH_RISK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "salary": ("salary", "gehalt", "compensation", "expectation", "vergütung", "lohn"),
    "legal_declaration": (
        "legal",
        "declaration",
        "convicted",
        "felony",
        "rechtlich",
        "vorstraf",
    ),
    "demographic_sensitive": (
        "disability",
        "behinderung",
        "gender",
        "ethnic",
        "race",
        "religion",
        "schwerbehindert",
    ),
    "work_authorization": (
        "work authorization",
        "sponsorship",
        "visa",
        "visa status",
        "arbeitserlaubnis",
        "aufenthaltstitel",
        "citizenship",
    ),
    "relocation": ("relocation", "relocate", "umzug", "willing to move"),
    "availability": ("availability", "start date", "verfügbar", "frühester"),
    "consent_signature": (
        "consent",
        "widerruf",
        "privacy policy",
        "datenschutz",
        "agree to the",
        "einwilligung",
    ),
}

# Field-label tokens → candidate profile metadata keys. Used ONLY for the
# Class-C mapping-defect check (the fact exists but the mapper failed); the
# values are never read or stored.
_LABEL_TOKEN_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "your", "ist", "ihre", "dein", "please", "enter", "what"}
)


def load_owner_policies(path: Path) -> list[OwnerPolicy]:
    """Load owner policies from a JSON file (knowledge level 2).

    File format::

        [{"policy_id": "discovery-source",
          "normalized_question": "how did you hear about us",
          "answer": "Sonstige",
          "description": "..."}]
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Owner policies file must be a JSON list: {path}")
    items = cast(list[dict[str, Any]], raw)
    return [OwnerPolicy.model_validate(item) for item in items]


def _label_tokens(label: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9]+", label.lower())
    return {t for t in tokens if t and t not in _LABEL_TOKEN_STOPWORDS}


def looks_high_risk(question: str, category: str | None) -> bool:
    """Conservative high-risk check: explicit category or keyword match."""
    if category and category in {
        "work_authorization",
        "availability",
        "salary",
        "relocation",
        "legal_declaration",
        "demographic_sensitive",
        "consent_signature",
    }:
        return True
    if not category:
        q = question.lower()
        for keywords in _HIGH_RISK_KEYWORDS.values():
            if any(k in q for k in keywords):
                return True
    return False


class PolicyEngine:
    """Deterministic policy gates. No LLM, no network, no heuristics that
    invent answers."""

    def __init__(self, owner_policies: list[OwnerPolicy] | None = None) -> None:
        self._policies: dict[str, OwnerPolicy] = {
            p.normalized_question: p for p in (owner_policies or [])
        }

    # -- intervention classification -----------------------------------

    def classify_intervention(
        self,
        view: InterventionView,
        *,
        candidate_fact_keys: list[str] | None = None,
        memory_lookup: Callable[[str], str | None] | None = None,
    ) -> PolicyClassification:
        """Classify one pending intervention (Class A/B/C/D)."""
        kind = (view.kind or "").lower()

        # Class D — hard blockers, never retried by the supervisor.
        if kind == "captcha":
            return PolicyClassification("D", ReasonCode.CAPTCHA)
        if kind == "login_required":
            return PolicyClassification("D", ReasonCode.LOGIN_REQUIRED)
        if kind == "unknown_page":
            return PolicyClassification("D", ReasonCode.NO_SAFE_NAVIGATION)

        # Class B — always human (non-field interventions).
        if kind == "missing_document":
            return PolicyClassification("B", ReasonCode.DOCUMENT_PROBLEM)
        if kind == "manual_upload_required":
            return PolicyClassification("B", ReasonCode.DOCUMENT_PROBLEM)
        if kind == "validation_error":
            return PolicyClassification("B", ReasonCode.UNKNOWN_REQUIRED_FIELD)
        if kind == "recovery":
            return PolicyClassification("B", ReasonCode.UNKNOWN_FAILURE)
        if kind == "review_before_submit":
            return PolicyClassification("B", ReasonCode.REVIEW_READY)

        # kind == field_answer (anything else falls through to the same
        # conservative handling).
        identity = view.field_label or view.question
        normalized = normalize_question(identity)

        # High-risk questions are human-only unless an explicit owner policy
        # already covers them.
        if looks_high_risk(identity, view.category):
            policy = self._policies.get(normalized)
            if policy is not None:
                return PolicyClassification(
                    "A",
                    ReasonCode.INTERVENTION_REQUIRED,
                    policy.answer,
                    AnswerSource.OWNER_POLICY,
                    policy.policy_id,
                )
            return PolicyClassification("B", ReasonCode.UNKNOWN_REQUIRED_FIELD)

        # Class A sources, in strict priority order.
        policy = self._policies.get(normalized)
        if policy is not None:
            return PolicyClassification(
                "A",
                ReasonCode.INTERVENTION_REQUIRED,
                policy.answer,
                AnswerSource.OWNER_POLICY,
                policy.policy_id,
            )

        if memory_lookup is not None:
            remembered = memory_lookup(normalized)
            if remembered:
                return PolicyClassification(
                    "A",
                    ReasonCode.INTERVENTION_REQUIRED,
                    remembered,
                    AnswerSource.ANSWER_MEMORY,
                    None,
                )

        # Class C — the fact exists in the candidate profile but the mapper
        # failed to use it: likely UAA mapping defect, report, don't answer.
        if candidate_fact_keys and self._fact_key_for_label(identity, candidate_fact_keys):
            return PolicyClassification("C", ReasonCode.UAA_MAPPING_DEFECT)

        # Everything else: human required. The model must never invent.
        return PolicyClassification("B", ReasonCode.UNKNOWN_REQUIRED_FIELD)

    @staticmethod
    def _fact_key_for_label(label: str, candidate_fact_keys: list[str]) -> str | None:
        """Return the candidate fact key whose tokens overlap the label.

        Only key NAMES are compared — candidate fact values are never read.
        """
        tokens = _label_tokens(label)
        if not tokens:
            return None
        for key in candidate_fact_keys:
            key_tokens = _label_tokens(key)
            if tokens & key_tokens:
                return key
        return None

    # -- decision validation -------------------------------------------

    def validate_decision(
        self,
        decision: SupervisorDecision,
        view: InterventionView | None,
        *,
        candidate_fact_keys: list[str] | None = None,
        memory_lookup: Callable[[str], str | None] | None = None,
    ) -> bool:
        """Re-validate a planner decision against the policy gates.

        Even a structurally valid decision is vetoed when it answers a
        Class B/D intervention, when the claimed source does not hold for
        the answer, or when MODEL_INFERENCE is used for anything other
        than a low-risk field answer.
        """
        if decision.action is not SupervisorAction.RESOLVE_INTERVENTION:
            # Non-answer actions carry no fabrication risk; other gates in
            # the service still apply (retry limits, review-only, etc.).
            return True
        if view is None:
            return False
        classification = self.classify_intervention(
            view,
            candidate_fact_keys=candidate_fact_keys,
            memory_lookup=memory_lookup,
        )
        if not classification.auto_resolvable:
            return False
        if decision.answer is None or decision.answer_source is None:
            return False
        if decision.answer_source is AnswerSource.MODEL_INFERENCE:
            # Strict restriction: model inference only for low-risk,
            # non-blocker field answers — and never a fabricated fact.
            if looks_high_risk(view.field_label or view.question, view.category):
                return False
            if view.kind.lower() != "field_answer":
                return False
            return True
        # Trusted sources must match what the policy engine itself found.
        if decision.answer_source is AnswerSource.OWNER_POLICY:
            return (
                classification.answer_source is AnswerSource.OWNER_POLICY
                and classification.answer == decision.answer
            )
        if decision.answer_source is AnswerSource.ANSWER_MEMORY:
            return (
                classification.answer_source is AnswerSource.ANSWER_MEMORY
                and classification.answer == decision.answer
            )
        if decision.answer_source in (
            AnswerSource.CANDIDATE_FACT,
            AnswerSource.JOB_SPECIFIC,
            AnswerSource.HUMAN,
        ):
            # Owner-supplied answers are accepted verbatim (the tool layer
            # received them from the owner, not from the model's own
            # invention) but only for interventions that are not Class B/D.
            return classification.decision_class == "A"
        return False


__all__ = [
    "DecisionClass",
    "PolicyClassification",
    "PolicyEngine",
    "load_owner_policies",
    "looks_high_risk",
]
