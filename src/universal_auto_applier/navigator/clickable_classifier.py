"""Clickable classifier — deterministic button classification.

Per ``ROADMAP.md`` WP 3.2, classifies clickables as:

- ``safe_apply`` — "apply", "apply now", "start application", "bewerben",
  "jetzt bewerben"
- ``safe_continue`` — "next", "continue", "save and continue", "weiter",
  "fortfahren"
- ``safe_upload`` — "upload", "attach", "upload resume", "upload cv"
- ``dangerous_submit`` — "submit", "submit application", "send application",
  "complete application", "finish", "bewerbung absenden", "absenden"
- ``login`` — "login", "sign in", "log in", "anmelden"
- ``unknown`` — anything else

Rules:
- The classifier is deterministic (rules first, no AI).
- It never marks a dangerous submit as safe.
- It covers English and German labels.
- Unknown text remains ``unknown``, not safe.
- Classification is case-insensitive and matches on normalized text
  (trimmed, collapsed whitespace).
- ``aria-label`` is checked in addition to visible text.
- Disabled or invisible elements are always ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from universal_auto_applier.core.statuses import ClickableClassification

# Safe apply terms (English + German).
_SAFE_APPLY_TERMS: frozenset[str] = frozenset(
    {
        "apply",
        "apply now",
        "start application",
        "start application process",
        "bewerben",
        "jetzt bewerben",
        "initiate application",
    }
)

# Safe continue terms.
_SAFE_CONTINUE_TERMS: frozenset[str] = frozenset(
    {
        "next",
        "continue",
        "save and continue",
        "save & continue",
        "weiter",
        "fortfahren",
        "next step",
        "proceed",
        "weitergehen",
    }
)

# Safe upload terms.
_SAFE_UPLOAD_TERMS: frozenset[str] = frozenset(
    {
        "upload",
        "upload resume",
        "upload cv",
        "upload cover letter",
        "attach resume",
        "attach cv",
        "attach file",
        "lebenslauf hochladen",
        "datei hochladen",
    }
)

# Dangerous submit terms.
_DANGEROUS_SUBMIT_TERMS: frozenset[str] = frozenset(
    {
        "submit",
        "submit application",
        "send application",
        "send",
        "complete application",
        "finish",
        "finish application",
        "bewerbung absenden",
        "absenden",
        "bewerbung senden",
    }
)

# Login terms.
_LOGIN_TERMS: frozenset[str] = frozenset(
    {
        "login",
        "log in",
        "sign in",
        "sign in here",
        "anmelden",
        "login here",
        "log in here",
    }
)

# Confidence values for each classification.
_CONFIDENCE_HIGH = 0.96
_CONFIDENCE_MEDIUM = 0.85
_CONFIDENCE_LOW = 0.0


@dataclass(frozen=True)
class ClassificationResult:
    """The result of classifying a single clickable element."""

    classification: ClickableClassification
    confidence: float


def _normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, trim, collapse whitespace."""
    if not text:
        return ""
    # Collapse multiple whitespace characters (including newlines) to a
    # single space, then trim.
    return re.sub(r"\s+", " ", text.strip()).lower()


def classify_clickable(
    *,
    text: str = "",
    aria_label: str = "",
    href: str = "",
    role: str = "",
    tag: str = "",
    enabled: bool = True,
    visible: bool = True,
) -> ClassificationResult:
    """Classify a clickable element based on its text and attributes.

    Args:
        text: The visible text of the element (button label, link text).
        aria_label: The ``aria-label`` attribute value.
        href: The ``href`` attribute (for links).
        role: The ``role`` attribute.
        tag: The HTML tag name (``button``, ``a``, ``input``).
        enabled: Whether the element is enabled (not disabled).
        visible: Whether the element is visible.

    Returns:
        A :class:`ClassificationResult` with the classification and
        confidence.

    Rules:
    - Disabled or invisible elements are always ``unknown``.
    - The classifier checks both ``text`` and ``aria_label`` (normalized).
    - If either matches a known term, that classification is used.
    - Dangerous submit is checked first and never overridden by a safe
      term (so "Submit and apply" is dangerous, not safe).
    - Login is checked before safe terms (so "Sign in to apply" is login,
      not safe_apply).
    - Unknown text remains ``unknown``.
    """
    # Disabled or invisible elements are never safe to click.
    if not enabled or not visible:
        return ClassificationResult(
            classification=ClickableClassification.UNKNOWN,
            confidence=_CONFIDENCE_LOW,
        )

    # Normalize both text sources for matching.
    norm_text = _normalize_text(text)
    norm_aria = _normalize_text(aria_label)

    # Check both sources. If either matches, use the match.
    # Priority: dangerous_submit > login > safe_apply > safe_continue >
    # safe_upload > unknown.
    # This ordering ensures "Submit and apply" is dangerous, not safe.

    for source in (norm_text, norm_aria):
        if not source:
            continue
        # Check dangerous submit first (highest priority).
        if source in _DANGEROUS_SUBMIT_TERMS:
            return ClassificationResult(
                classification=ClickableClassification.DANGEROUS_SUBMIT,
                confidence=_CONFIDENCE_HIGH,
            )
        # Also check if any dangerous term is a substring (e.g. "submit form").
        for term in _DANGEROUS_SUBMIT_TERMS:
            if term in source:
                return ClassificationResult(
                    classification=ClickableClassification.DANGEROUS_SUBMIT,
                    confidence=_CONFIDENCE_MEDIUM,
                )

    for source in (norm_text, norm_aria):
        if not source:
            continue
        # Check login.
        if source in _LOGIN_TERMS:
            return ClassificationResult(
                classification=ClickableClassification.LOGIN,
                confidence=_CONFIDENCE_HIGH,
            )
        for term in _LOGIN_TERMS:
            if term in source:
                return ClassificationResult(
                    classification=ClickableClassification.LOGIN,
                    confidence=_CONFIDENCE_MEDIUM,
                )

    for source in (norm_text, norm_aria):
        if not source:
            continue
        # Check safe apply.
        if source in _SAFE_APPLY_TERMS:
            return ClassificationResult(
                classification=ClickableClassification.SAFE_APPLY,
                confidence=_CONFIDENCE_HIGH,
            )
        for term in _SAFE_APPLY_TERMS:
            if term in source:
                return ClassificationResult(
                    classification=ClickableClassification.SAFE_APPLY,
                    confidence=_CONFIDENCE_MEDIUM,
                )

    for source in (norm_text, norm_aria):
        if not source:
            continue
        # Check safe continue.
        if source in _SAFE_CONTINUE_TERMS:
            return ClassificationResult(
                classification=ClickableClassification.SAFE_CONTINUE,
                confidence=_CONFIDENCE_HIGH,
            )
        for term in _SAFE_CONTINUE_TERMS:
            if term in source:
                return ClassificationResult(
                    classification=ClickableClassification.SAFE_CONTINUE,
                    confidence=_CONFIDENCE_MEDIUM,
                )

    for source in (norm_text, norm_aria):
        if not source:
            continue
        # Check safe upload.
        if source in _SAFE_UPLOAD_TERMS:
            return ClassificationResult(
                classification=ClickableClassification.SAFE_UPLOAD,
                confidence=_CONFIDENCE_HIGH,
            )
        for term in _SAFE_UPLOAD_TERMS:
            if term in source:
                return ClassificationResult(
                    classification=ClickableClassification.SAFE_UPLOAD,
                    confidence=_CONFIDENCE_MEDIUM,
                )

    # No match found.
    return ClassificationResult(
        classification=ClickableClassification.UNKNOWN,
        confidence=_CONFIDENCE_LOW,
    )


__all__ = [
    "ClassificationResult",
    "classify_clickable",
]
