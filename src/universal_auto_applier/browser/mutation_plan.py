"""WQ-7C pre-mutation machine-readable plan.

Before ANY field on a live ATS form is mutated, the full plan is built,
frozen, and hashed. The plan records, per discovered field:

- the selector, label, name, type and options as observed in the DOM,
- the canonical intended-field category and its risk level,
- the value source (``candidate_profile`` / ``document_path`` / none),
- the proposed value and its confidence,
- the resolver path used to obtain the value,
- a decision: ``mutate`` / ``skip`` / ``block`` / ``needs_intervention``,
- a human-readable explanation.

Safety rules that the plan enforces (deterministically, no LLM):

- Only values sourced from the synthetic candidate profile (deterministic
  label mapping at ``CONFIDENCE_THRESHOLD`` or above) or approved synthetic
  document paths (SHA-256 member of the approved set) may be decided
  ``mutate``. A "missing" interpretation is never auto-answered.
- Any other source (LLM, answer memory, adapter default, job content) is
  decided ``skip`` / ``block`` / ``needs_intervention`` in synthetic mode.
- Never mapped fields on required controls become ``needs_intervention``,
  never a fabricated value.
- Fields of unknown type or password-like controls are ``block``.

The plan is immutable after creation. :meth:`MutationPlan.plan_hash` covers
the canonical JSON serialization of the full plan, so execution evidence can
prove the executed plan is the frozen plan.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FormField,
)
from universal_auto_applier.core.question_models import ApplicationQuestion
from universal_auto_applier.form_engine.field_mapper import (
    CONFIDENCE_THRESHOLD,
    map_field,
)
from universal_auto_applier.llm.question_classifier import classify_question
from universal_auto_applier.synthetic_profile import sha256_file

MutationDecision = Literal["mutate", "skip", "block", "needs_intervention"]

# Sources that are allowed to mutate in WQ-7C synthetic mode.
_MUTATEABLE_SOURCES = frozenset({"candidate_profile", "document_path"})

# Categories that must NOT be auto-mutated even when a synthetic value could
# be mapped, because answering them would fabricate a legal or sensitive
# declaration, or would falsely answer "No" to a qualification question.
_NEVER_MUTATE_CATEGORIES = frozenset(
    {
        "legal_declaration",
        "consent_signature",
        "demographic_sensitive",
        "work_authorization",
        "availability",
    }
)


class MutationPlanEntry(BaseModel):
    """One field's pre-mutation decision, serialized as evidence."""

    model_config = {"frozen": True}

    selector: str
    label: str = ""
    name: str = ""
    field_type: str = "unknown"
    required: bool = False
    options: list[str] = Field(default_factory=list[str])
    category: str = ""
    risk_level: str = ""
    value_source: str = ""
    proposed_value: str | None = None
    confidence: float | None = None
    resolver_path: str = ""
    decision: MutationDecision = "skip"
    explanation: str = ""


class MutationPlan(BaseModel):
    """Frozen, hash-verifiable WQ-7C mutation plan."""

    model_config = {"frozen": True}

    application_id: str
    page_url: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    document_hashes_approved: frozenset[str] = Field(default_factory=frozenset)
    budget: int = Field(..., ge=1)
    entries: list[MutationPlanEntry] = Field(default_factory=list[MutationPlanEntry])

    @property
    def plan_hash(self) -> str:
        """SHA-256 of the canonical JSON serialization of this plan.

        The volatile ``generated_at`` timestamp is excluded so that two
        plans with identical structure hash identically (perfect for
        re-verification). Everything else — every field decision, value,
        and explanation — is covered.
        """
        data = self.model_dump(mode="json")
        data.pop("generated_at", None)
        canonical = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def mutate_count(self) -> int:
        return sum(1 for e in self.entries if e.decision == "mutate")

    @property
    def skip_count(self) -> int:
        return sum(1 for e in self.entries if e.decision == "skip")

    @property
    def block_count(self) -> int:
        return sum(1 for e in self.entries if e.decision == "block")

    @property
    def intervention_count(self) -> int:
        return sum(1 for e in self.entries if e.decision == "needs_intervention")

    @property
    def mutate_entries(self) -> list[MutationPlanEntry]:
        """Entries the execution layer is allowed to mutate, in order."""
        return [e for e in self.entries if e.decision == "mutate"]


def _question_text(field: FormField) -> str:
    """Compose the text a question classifier should see for a field."""
    bits = [field.label, field.name, field.nearby_text]
    return " ".join(b for b in bits if b)


def _as_question(field: FormField, page_url: str) -> ApplicationQuestion:
    """Wrap a FormField as the ApplicationQuestion the classifier expects."""
    return ApplicationQuestion(
        question_text=_question_text(field),
        field_selector=field.selector,
        field_type=field.type,
        options=[o.label or o.value for o in field.options],
        required=field.required,
        nearby_text=field.nearby_text,
        page_url=page_url,
    )


def _is_password_like(field: FormField) -> bool:
    """True for password controls (never mutated)."""
    base = f"{field.label} {field.name}".lower()
    return "password" in base or "passwort" in base


def _declared_synthetic_values(candidate: CandidateProfile) -> frozenset[str]:
    """The set of VALUES the synthetic identity explicitly declares.

    The deterministic mapper also reports ``candidate_profile`` as the
    source for values derived from CV evidence (e.g. a "Yes" for an
    experience question backed by the CV text). WQ-7C mutation must only
    ever enter values that the synthetic identity actually states — never
    an inference. This allowlist restricts mutation to exactly those
    declared values.

    Booleans are included as the exact "Yes"/"No" string their declared
    value produces; a blanket "Yes"/"No" is NOT included so evidence-
    derived answers cannot sneak in.
    """
    allowed: set[str] = set()
    for value in candidate.model_dump().values():
        if value is None or isinstance(value, (list, dict)):
            continue
        if isinstance(value, bool):
            allowed.add("Yes" if value else "No")
        else:
            allowed.add(str(value))
    return frozenset(allowed)  # noqa: TC006


def _value_fits_options(field_type: str, proposed: str, options: list[str]) -> bool:
    """True when ``proposed`` can select an existing option on this control.

    Mirrors the executor's typed-answer validation for select/radio/checkbox
    so the plan records the same outcome the execution would produce. Missing
    options mean an unknown option-set — the caller decides separately.
    """
    if field_type not in ("select", "radio", "checkbox"):
        return True
    if not options:
        return True
    desired = _normalize_option(proposed)
    return any(_normalize_option(opt) == desired for opt in options)


def _normalize_option(value: str) -> str:
    """Normalize an option/value for comparison (yes/no aliases)."""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    aliases = {
        "true": "yes",
        "1": "yes",
        "ja": "yes",
        "false": "no",
        "0": "no",
        "nein": "no",
    }
    return aliases.get(normalized, normalized)


def build_mutation_plan(
    fields: list[FormField],
    candidate: CandidateProfile,
    job: ApplicationJob,
    *,
    approved_document_hashes: frozenset[str],
    budget: int,
    application_id: str,
    page_url: str,
) -> MutationPlan:
    """Build a frozen mutation plan for ``fields``.

    The plan is authoritative: execution applies exactly the entries whose
    decision is ``mutate``, consuming the budget in order, and never touches
    anything else.
    """
    entries: list[MutationPlanEntry] = []
    declared_values = _declared_synthetic_values(candidate)
    for field in fields:
        category, risk = classify_question(_as_question(field, page_url))
        entry_kwargs: dict[str, Any] = dict(
            selector=field.selector,
            label=field.label,
            name=field.name,
            field_type=field.type,
            required=field.required,
            options=[o.label or o.value for o in field.options],
            category=str(category.value),
            risk_level=str(risk.value),
        )

        if field.type == "unknown" or _is_password_like(field):
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="block",
                    explanation="Unknown or password-like control; never mutated.",
                )
            )
            continue

        if category.value in _NEVER_MUTATE_CATEGORIES:
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="skip",
                    explanation=(
                        f"Category {category.value} is never auto-answered in synthetic mode."
                    ),
                )
            )
            continue

        mapping = map_field(field, candidate, job)
        if mapping is None:
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="needs_intervention" if field.required else "skip",
                    explanation=(
                        "No deterministic mapping from the synthetic candidate; not fabricated."
                        if field.required
                        else "No deterministic mapping; skipped."
                    ),
                )
            )
            continue

        if mapping.source not in _MUTATEABLE_SOURCES:
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="block",
                    explanation=(
                        f"Source '{mapping.source}' is not allowed in synthetic mutation mode."
                    ),
                )
            )
            continue

        if mapping.source == "document_path":
            path = Path(str(mapping.value))
            digest = sha256_file(path) if path.is_file() else ""
            if not path.is_file():
                entries.append(
                    MutationPlanEntry(
                        **entry_kwargs,
                        decision="needs_intervention" if field.required else "skip",
                        explanation="Document path does not exist.",
                    )
                )
                continue
            if digest not in approved_document_hashes:
                entries.append(
                    MutationPlanEntry(
                        **entry_kwargs,
                        decision="block",
                        explanation=(
                            "Document is not among the approved synthetic "
                            "document hashes; upload refused."
                        ),
                    )
                )
                continue
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="mutate",
                    proposed_value=str(mapping.value),
                    confidence=mapping.confidence,
                    value_source="document_path",
                    resolver_path="approved_document_hash",
                    explanation=mapping.explanation,
                )
            )
            continue

        if mapping.confidence < CONFIDENCE_THRESHOLD:
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="skip",
                    explanation=(
                        f"Confidence {mapping.confidence:.2f} below threshold "
                        f"{CONFIDENCE_THRESHOLD}; skipped for precision."
                    ),
                )
            )
            continue

        # Only declared synthetic identity values may be entered. Mappings
        # derived from CV evidence also carry source `candidate_profile`,
        # but their value is an inference (e.g. "Yes" because the CV text
        # mentions a skill) — never allowed as a mutation.
        if str(mapping.value) not in declared_values:
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="skip",
                    explanation=(
                        f"Value {mapping.value!r} is not a declared synthetic "
                        "identity fact; not entered."
                    ),
                )
            )
            continue

        # The proposed value must be selectable on this control (when the
        # control has known options), else it cannot be filled and must not
        # be recorded as a planned mutation.
        options = [o.label or o.value for o in field.options]
        if not _value_fits_options(field.type, str(mapping.value), options):
            entries.append(
                MutationPlanEntry(
                    **entry_kwargs,
                    decision="needs_intervention" if field.required else "skip",
                    explanation=(
                        f"Proposed value {mapping.value!r} does not match any "
                        "available option; not entered."
                    ),
                )
            )
            continue

        entries.append(
            MutationPlanEntry(
                **entry_kwargs,
                decision="mutate",
                proposed_value=str(mapping.value),
                confidence=mapping.confidence,
                value_source="candidate_profile",
                resolver_path="deterministic_label",
                explanation=mapping.explanation,
            )
        )

    return MutationPlan(
        application_id=application_id,
        page_url=page_url,
        document_hashes_approved=approved_document_hashes,
        budget=budget,
        entries=entries,
    )


__all__ = [
    "MutationDecision",
    "MutationPlan",
    "MutationPlanEntry",
    "build_mutation_plan",
]
