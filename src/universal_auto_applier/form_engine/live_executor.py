"""Execute deterministic form mappings against a rendered Playwright page.

This module fills controls and uploads documents. It has no submission API
and never clicks buttons, which keeps final-submit authority in the runner's
review gate.

The LLM question resolver (:mod:`universal_auto_applier.llm.question_resolver`)
is integrated via :func:`execute_live_form_with_llm`, which extends
:func:`execute_live_form` with grounded LLM answers for questions that
deterministic mapping cannot resolve. The LLM path never invents personal
facts and never clicks final submit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Locator, Page

from universal_auto_applier.browser.live_models import LiveFieldRecord, LiveUploadRecord
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldOption,
    FormField,
)
from universal_auto_applier.form_engine.fill_engine import fill_form

logger = logging.getLogger("universal_auto_applier.form_engine.live_executor")

_CONTROL_SELECTOR = (
    "input:not([type='hidden']):not([type='button']):not([type='submit'])"
    ":not([type='reset']):not([type='image']), textarea, select"
)
_FIELD_METADATA_JS = r"""
(el) => {
  const text = (node) => (node && (node.innerText || node.textContent) || '').trim();
  const id = el.id || '';
  const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
  const wrapper = el.closest('label');
  const fieldset = el.closest('fieldset');
  const legend = fieldset ? fieldset.querySelector('legend') : null;
  const container = el.closest(
    '[role="group"], .form-group, .field-border, .field, .question, .control'
  );
  const label = text(explicit) || text(wrapper) || el.getAttribute('aria-label') ||
    el.getAttribute('placeholder') || el.getAttribute('name') || id;
  const nearby = text(legend) || text(container) || label;
  return {
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    id,
    name: el.getAttribute('name') || '',
    label,
    nearby: nearby.slice(0, 1000),
    required: el.required || el.getAttribute('aria-required') === 'true' ||
      /\*/.test(label) || /\*/.test(nearby),
    value: el.value || '',
    checked: el.checked || false,
    placeholder: el.getAttribute('placeholder') || ''
  };
}
"""

# Dedicated metadata extractor for radio groups. Returns the question text
# (from fieldset legend, aria-labelledby, aria-label, or nearest group
# container) and the currently-checked radio's value.
#
# This must NOT use the option label (e.g. "Yes"/"No") as the question text,
# because that loses the actual question being asked.
_RADIO_GROUP_METADATA_JS = r"""
(radioArray) => {
  const text = (node) => (node && (node.innerText || node.textContent) || '').trim();
  const radios = radioArray || [];
  if (radios.length === 0) return { questionText: '', selectedValue: '' };
  const first = radios[0];

  // 1. Fieldset legend (highest priority).
  const fieldset = first.closest('fieldset');
  const legend = fieldset ? fieldset.querySelector('legend') : null;
  const legendText = text(legend);

  // 2. aria-labelledby on any radio (points to a separate label element).
  let labelledByText = '';
  for (const r of radios) {
    const lb = r.getAttribute('aria-labelledby');
    if (lb) {
      const el = document.getElementById(lb);
      if (el) { labelledByText = text(el); break; }
    }
  }

  // 3. aria-label on any radio.
  let ariaLabelText = '';
  for (const r of radios) {
    const al = r.getAttribute('aria-label');
    if (al) { ariaLabelText = al.trim(); break; }
  }

  // 4. Nearest [role="group"] or .form-group / .field / .question container.
  const container = first.closest(
    '[role="group"], .form-group, .field-border, .field, .question, .control'
  );
  let containerAriaLabel = '';
  let containerLabelledByText = '';
  if (container) {
    const cal = container.getAttribute('aria-label');
    if (cal) containerAriaLabel = cal.trim();
    const clb = container.getAttribute('aria-labelledby');
    if (clb) {
      const el = document.getElementById(clb);
      if (el) containerLabelledByText = text(el);
    }
  }

  // Question text priority: legend > aria-labelledby (radio) > aria-label
  // (radio) > container aria-labelledby > container aria-label > container
  // text > name attribute. Never falls back to an option label.
  const questionText = legendText
    || labelledByText
    || ariaLabelText
    || containerLabelledByText
    || containerAriaLabel
    || (container ? text(container) : '')
    || first.getAttribute('name') || '';

  // Selected value: the value of the checked radio, if any.
  let selectedValue = '';
  for (const r of radios) {
    if (r.checked) { selectedValue = r.value || ''; break; }
  }

  return { questionText, selectedValue };
}
"""


@dataclass
class _LiveFieldTarget:
    token: str
    selector_hint: str
    frame_url: str
    field: FormField
    locator: Locator


@dataclass
class LiveFormExecution:
    """Structured result of one rendered form page fill."""

    fields: list[LiveFieldRecord] = field(default_factory=list[LiveFieldRecord])
    uploads: list[LiveUploadRecord] = field(default_factory=list[LiveUploadRecord])
    validation_errors: list[str] = field(default_factory=list[str])
    required_unresolved: int = 0
    filled: int = 0


def _metadata(locator: Locator) -> dict[str, Any]:
    raw = locator.evaluate(_FIELD_METADATA_JS)
    if not isinstance(raw, dict):
        return {}
    return cast(dict[str, Any], raw)


def _field_type(metadata: dict[str, Any]) -> str:
    tag = str(metadata.get("tag", "")).lower()
    input_type = str(metadata.get("type") or "text").lower()
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    return {
        "text": "text",
        "email": "email",
        "tel": "phone",
        "url": "text",
        "number": "number",
        "date": "date",
        "datetime-local": "date",
        "file": "file",
        "radio": "radio",
        "checkbox": "checkbox",
        "password": "unknown",
    }.get(input_type, "unknown")


def _selector_hint(metadata: dict[str, Any], tag: str, index: int) -> str:
    element_id = str(metadata.get("id", ""))
    if element_id:
        return f"{tag}[id={element_id!r}]"
    name = str(metadata.get("name", ""))
    if name:
        return f"{tag}[name={name!r}]"
    return f"{tag}[{index}]"


def _field_options(locator: Locator, field_type: str) -> list[FieldOption]:
    if field_type == "select":
        options: list[FieldOption] = []
        option_locators = locator.locator("option")
        for index in range(option_locators.count()):
            option = option_locators.nth(index)
            options.append(
                FieldOption(
                    value=option.get_attribute("value") or option.inner_text(),
                    label=option.inner_text().strip(),
                )
            )
        return options
    if field_type == "checkbox":
        meta = _metadata(locator)
        return [
            FieldOption(
                value=str(meta.get("value", "on")),
                label=str(meta.get("label", "")),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Stable field identity
# ---------------------------------------------------------------------------
#
# Field tokens must NOT depend on extraction order or list index. A field
# that appears at index 5 in one observation and index 4 in the next (because
# a new field was inserted above it, or a conditional field was revealed)
# must keep the same token so that:
#   - later fills can supersede earlier interventions;
#   - CLI persistence does not create a stale pending intervention for a
#     field that was actually filled;
#   - re-runs are idempotent.
#
# Canonical identity is built from stable DOM properties:
#   - frame identity:
#       * the top (main) frame is always the literal string "main", so
#         dynamic page URLs (query strings, fragments, SPA route changes)
#         do not change top-frame field identity;
#       * iframe URLs are stripped of their query string and fragment
#         (volatile session tokens, timestamps) — scheme+host+path only;
#   - field type;
#   - element id (most stable when present);
#   - input name attribute;
#   - for radio groups: (frame_id, "radio", group name, normalized
#     question label) — one token per group, shared across all options;
#   - normalized question/group label (disambiguates fields that share id
#     and name, e.g. multiple unnamed text inputs in different fieldsets).
#
# The token is a short SHA-256 hex prefix of the canonical string, prefixed
# with `lf-` (live field) so it is visually distinct from the legacy
# `live-field-0-N` positional tokens.
#
# Legacy compatibility: existing pending interventions created with the
# old positional tokens (``live-field-0-N``) CANNOT be auto-matched to
# the new stable tokens (``lf-...``). They require local-data cleanup
# (manual resolution via the dashboard or a one-time cleanup script).
# This is documented honestly in _persist_interventions and is NOT
# silently papered over.

_FRAME_MAIN_SENTINEL = "main"


def _frame_identity(frame_url: str, is_main_frame: bool = False) -> str:
    """Normalize a frame URL into a stable frame identifier.

    The top frame is always returned as the literal string ``main`` so
    that dynamic page URLs (query strings, fragments, SPA route changes)
    do not unnecessarily change field identity for top-frame fields.

    Iframe URLs are stripped of their query string and fragment so that
    volatile parameters (session tokens, timestamps, cache-busters) do
    not change the iframe's identity across observations. The scheme,
    host, and path are preserved — they identify the embedded document
    (e.g. ``https://boards.greenhouse.io/embed/job_applications/123``).

    Args:
        frame_url: The frame's current URL.
        is_main_frame: True if this is the page's main (top) frame.

    Returns:
        A stable frame identifier string.
    """
    if is_main_frame:
        return _FRAME_MAIN_SENTINEL
    if not frame_url or frame_url == "about:blank":
        return _FRAME_MAIN_SENTINEL
    # Strip volatile query strings and fragments from iframe URLs.
    # Keep scheme + host + path (stable), drop ?query and #fragment.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(frame_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _normalize_label_for_token(label: str) -> str:
    """Normalize a label/question text for inclusion in the token.

    Lowercases, collapses whitespace, strips non-alphanumeric characters.
    Truncated to 80 chars so a very long legend does not bloat the hash
    input. Empty labels normalize to empty string (and are excluded from
    the canonical string by the caller).
    """
    if not label:
        return ""
    normalized = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
    return normalized[:80]


def compute_field_token(
    *,
    frame_id: str,
    field_type: str,
    element_id: str,
    name: str,
    label: str,
    is_radio_group: bool = False,
) -> str:
    """Compute a stable field token from canonical DOM properties.

    Args:
        frame_id: The already-normalized frame identity (from
            :func:`_frame_identity`). This is NOT the raw frame URL —
            the caller must normalize first so that query strings,
            fragments, and main-frame URL changes do not affect the
            token.
        field_type: The resolved field type (text, email, radio, etc.).
        element_id: The element's ``id`` attribute (empty if absent).
        name: The element's ``name`` attribute (empty if absent).
        label: The resolved question/label text (legend, aria-label, etc.).
        is_radio_group: True if this is a radio group (all options share
            one token keyed on the group ``name`` attribute + question
            label).

    Returns:
        A short stable token like ``lf-a1b2c3d4``.

    Identity rules:
    - Radio groups: token is derived from (frame_id, "radio", group
      name, normalized question label). All options in the same group
      produce the SAME token. The question label is included so two
      radio groups that happen to share a ``name`` attribute but ask
      different questions remain distinct (rare but possible in SPA
      frameworks that reuse names across visually distinct groups).
    - Non-radio fields: token is derived from (frame_id, type, id, name,
      normalized label). When ``id`` is present it dominates; otherwise
      ``name`` dominates; otherwise the normalized label is used.
    - Two fields with the same id+name+label in the same frame collide
      intentionally (they are the same field on re-extraction).
    - Two fields with similar labels but different ids/names stay distinct.
    - The token contains NO extraction index, NO list position, and NO
      volatile URL component.
    """
    norm_label = _normalize_label_for_token(label)

    if is_radio_group:
        # Radio group identity: frame + "radio" + group name + question
        # label. The question label disambiguates groups that share a
        # name attribute but ask different questions.
        canonical = f"radio|{frame_id}|{name}|{norm_label}"
    else:
        # Build the canonical string from the most-stable identifiers first.
        parts: list[str] = [frame_id, field_type]
        if element_id:
            parts.append(f"id={element_id}")
        if name:
            parts.append(f"name={name}")
        if norm_label:
            parts.append(f"label={norm_label}")
        canonical = "|".join(parts)

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"lf-{digest}"


def _extract_live_fields(page: Page) -> list[_LiveFieldTarget]:
    targets: list[_LiveFieldTarget] = []
    # Radio groups are deduplicated by (frame_id, name) — a stable identity
    # that does not depend on extraction order. The first radio we encounter
    # for a given (frame, name) pair becomes the group's representative.
    processed_radio_groups: set[tuple[str, str]] = set()

    main_frame = page.main_frame
    for frame in page.frames:
        is_main = frame == main_frame
        frame_id = _frame_identity(frame.url, is_main_frame=is_main)
        controls = frame.locator(_CONTROL_SELECTOR)
        try:
            control_count = min(controls.count(), 250)
        except Exception:
            continue

        for control_index in range(control_count):
            locator = controls.nth(control_index)
            try:
                meta = _metadata(locator)
            except Exception:
                continue
            css_classes = locator.get_attribute("class") or ""
            if "chosen-search-input" in css_classes:
                # Chosen.js mirrors a hidden native <select> with an internal
                # search input. The native select is the real field; treating
                # this helper as a second required field creates duplicates.
                continue
            field_type = _field_type(meta)
            is_file = field_type == "file"
            try:
                if not is_file and not locator.is_visible():
                    element_id = str(meta.get("id", ""))
                    chosen_visible = (
                        bool(element_id)
                        and frame.locator(f"[id={json.dumps(element_id + '_chosen')}]").is_visible()
                    )
                    if field_type != "select" or not chosen_visible:
                        continue
                if not locator.is_enabled():
                    continue
            except Exception:
                continue

            name = str(meta.get("name", ""))
            element_id = str(meta.get("id", ""))
            label_text = str(meta.get("label", ""))
            target_locator = locator
            options: list[FieldOption] = _field_options(locator, field_type)
            is_radio_group = False

            if field_type == "radio" and name:
                # Stable radio-group identity: (frame_id, name). This is
                # independent of which radio option the iterator reached
                # first, so inserting/removing/revealing fields above the
                # group does not change the group's token.
                group_key = (frame_id, name)
                if group_key in processed_radio_groups:
                    continue
                processed_radio_groups.add(group_key)
                group_selector = f"input[type='radio'][name={json.dumps(name)}]"
                target_locator = frame.locator(group_selector)
                options = []
                required = False
                question_label = ""
                selected_value = ""
                for option_index in range(target_locator.count()):
                    option_locator = target_locator.nth(option_index)
                    option_meta = _metadata(option_locator)
                    option_value = str(option_meta.get("value", ""))
                    option_label = str(option_meta.get("label", ""))
                    is_checked = bool(option_meta.get("checked", False))
                    options.append(
                        FieldOption(
                            value=option_value,
                            label=option_label,
                            selected=is_checked,
                        )
                    )
                    required = required or bool(option_meta.get("required", False))
                    if is_checked:
                        selected_value = option_value
                    # Capture the question text from the first radio's
                    # nearby text (legend / aria / container). This is the
                    # fallback if the dedicated radio-group JS below fails
                    # or returns empty.
                    if not question_label:
                        question_label = str(option_meta.get("nearby", ""))

                # Use the dedicated radio-group metadata extractor for the
                # authoritative question text and selected value. It knows
                # how to distinguish the question (legend/aria) from the
                # option labels (Yes/No).
                try:
                    group_meta_raw = target_locator.evaluate_all(_RADIO_GROUP_METADATA_JS)
                    if isinstance(group_meta_raw, dict):
                        group_meta: dict[str, Any] = cast(dict[str, Any], group_meta_raw)
                        group_question = str(group_meta.get("questionText", "")).strip()
                        if group_question:
                            question_label = group_question
                        group_selected = str(group_meta.get("selectedValue", "")).strip()
                        if group_selected:
                            selected_value = group_selected
                except Exception:
                    # Fallback to the per-option nearby text already captured.
                    pass

                # Override the per-radio metadata so the FormField reflects
                # the logical question, not the first option's label.
                meta["label"] = question_label
                meta["nearby"] = question_label
                meta["value"] = selected_value
                meta["required"] = required
                label_text = question_label
                is_radio_group = True

            # Stable token: derived from canonical DOM properties, NOT from
            # the iteration index. Same field keeps the same token across
            # re-extractions even if the DOM changes above/below it.
            token = compute_field_token(
                frame_id=frame_id,
                field_type=field_type,
                element_id=element_id,
                name=name,
                label=label_text,
                is_radio_group=is_radio_group,
            )

            tag = str(meta.get("tag", "input"))
            field_model = FormField(
                selector=token,
                name=name,
                label=str(meta.get("label", "")),
                type=field_type,
                required=bool(meta.get("required", False)),
                options=options,
                current_value=str(meta.get("value", "")),
                nearby_text=str(meta.get("nearby", "")),
                confidence=0.95 if meta.get("label") else 0.6,
            )
            targets.append(
                _LiveFieldTarget(
                    token=token,
                    selector_hint=_selector_hint(meta, tag, control_index),
                    frame_url=frame.url,
                    field=field_model,
                    locator=target_locator,
                )
            )
    return targets


def _normalize_option(value: str) -> str:
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


def _select_option(locator: Locator, value: str) -> str:
    """Select a <select> option matching ``value``.

    Returns the LABEL of the option that was actually selected (e.g.
    ``"Germany"`` when the proposed value was ``"de"`` or ``"Germany"``).
    This lets the caller record the actual DOM selection rather than the
    proposed value, which may have been a normalized alias.
    """
    desired = _normalize_option(value)
    options = locator.locator("option")
    for index in range(options.count()):
        option = options.nth(index)
        option_value = option.get_attribute("value") or ""
        option_label = option.inner_text().strip()
        if desired in {_normalize_option(option_value), _normalize_option(option_label)}:
            locator.select_option(value=option_value, force=True)
            return option_label or option_value
    raise ValueError(f"no select option matches {value!r}")


def _choose_radio(locator: Locator, value: str) -> str:
    """Check the radio option matching ``value``.

    Returns the VALUE (or label, whichever is non-empty) of the radio
    that was actually checked. For a German form with options
    ``[value="ja", value="nein"]``, proposing ``"Yes"`` checks the
    ``"ja"`` radio and returns ``"ja"`` — NOT ``"Yes"``.
    """
    desired = _normalize_option(value)
    for index in range(locator.count()):
        option = locator.nth(index)
        meta = _metadata(option)
        option_value = str(meta.get("value", ""))
        option_label = str(meta.get("label", ""))
        candidates = {
            _normalize_option(option_value),
            _normalize_option(option_label),
        }
        if desired in candidates:
            option.check()
            # Prefer the value (what the form actually submits); fall back
            # to the label if the value is empty (some forms use empty
            # values and rely on the label).
            return option_value or option_label
    raise ValueError(f"no radio option matches {value!r}")


def _set_checkbox(locator: Locator, value: str) -> str:
    """Check or uncheck the checkbox based on ``value``.

    Returns the normalized state that was actually applied: ``"yes"`` if
    checked, ``"no"`` if unchecked.
    """
    desired = _normalize_option(value)
    if desired == "yes":
        locator.check()
        return "yes"
    if desired == "no":
        locator.uncheck()
        return "no"
    raise ValueError(f"checkbox answer must be yes/no, got {value!r}")


def validate_typed_answer(
    field_type: str,
    value: str | None,
    options: list[FieldOption] | None = None,
) -> tuple[bool, str]:
    """Validate a proposed answer against the field's declared type.

    Runs BEFORE Playwright touches the field. Invalid answers are routed to
    ``intervention_needed`` (never ``failed``). A safely-unresolved question
    must not be classified as a failure.

    Returns:
        ``(True, "")`` if the value is acceptable for the field type.
        ``(False, reason)`` if the value cannot be filled safely.

    Rules:
    - ``number``: must parse as int or float (e.g. "5", "3.5"). "Yes" is
      rejected.
    - ``date``: must look like a calendar date (YYYY-MM-DD, DD.MM.YYYY, or
      MM/DD/YYYY). Free-form text is rejected.
    - ``checkbox``: must normalize to yes/no/true/false.
    - ``select`` and ``radio``: must match one of the available options
      (after normalization). Answers outside the option set are rejected.
    - ``text`` / ``textarea`` / ``email`` / ``phone``: any non-empty string
      is accepted (the LLM validator already enforces evidence and length).
    """
    if value is None:
        return False, "empty value"
    candidate = str(value).strip()
    if not candidate:
        return False, "empty value"

    if field_type == "number":
        try:
            float(candidate)
        except ValueError:
            return False, f"not a number: {candidate!r}"
        return True, ""

    if field_type == "date":
        # Accept YYYY-MM-DD, DD.MM.YYYY, MM/DD/YYYY. Reject free-form text.
        if not re.match(
            r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}\.\d{1,2}\.\d{4}$|^\d{1,2}/\d{1,2}/\d{4}$",
            candidate,
        ):
            return False, f"not a date: {candidate!r}"
        return True, ""

    if field_type == "checkbox":
        desired = _normalize_option(candidate)
        if desired not in ("yes", "no"):
            return False, f"checkbox answer must be yes/no, got {candidate!r}"
        return True, ""

    if field_type in ("select", "radio"):
        opts = options or []
        if not opts:
            # No options known — accept and let Playwright raise if it fails.
            return True, ""
        desired = _normalize_option(candidate)
        for opt in opts:
            candidates = {
                _normalize_option(opt.value),
                _normalize_option(opt.label),
            }
            if desired in candidates:
                return True, ""
        option_labels = [opt.label or opt.value for opt in opts]
        return False, f"answer {candidate!r} not in options {option_labels!r}"

    # text / textarea / email / phone: any non-empty string is acceptable.
    return True, ""


def _execute_field(target: _LiveFieldTarget, value: str) -> str:
    """Fill the field with ``value`` and return the actual DOM-recorded value.

    For text/email/phone/textarea/date/number fields, the returned value is
    the input value (what was typed).

    For select/radio/checkbox fields, the returned value is the LABEL or
    VALUE of the option that was actually selected/checked — NOT the
    proposed value. This is critical for cross-language forms: if the
    proposed answer was ``"Yes"`` but the form's options are
    ``["ja", "nein"]``, the actual DOM selection is ``"ja"`` and that is
    what gets recorded in ``filled_value`` and ``selected_value``.
    """
    field_type = target.field.type
    if field_type in {"text", "email", "phone", "textarea", "date", "number"}:
        target.locator.fill(value)
        return value
    elif field_type == "select":
        return _select_option(target.locator, value)
    elif field_type == "radio":
        return _choose_radio(target.locator, value)
    elif field_type == "checkbox":
        return _set_checkbox(target.locator, value)
    elif field_type == "file":
        target.locator.set_input_files(value)
        return value
    else:
        raise ValueError(f"unsupported live field type: {field_type}")


def _document_kind(target: _LiveFieldTarget) -> str:
    descriptor = f"{target.field.label} {target.field.nearby_text} {target.field.name}".lower()
    if "cover" in descriptor or "anschreiben" in descriptor:
        return "cover_letter"
    if "resume" in descriptor or "cv" in descriptor or "lebenslauf" in descriptor:
        return "cv"
    return "unknown"


def _validation_errors(page: Page) -> list[str]:
    errors: list[str] = []
    for frame in page.frames:
        locators = frame.locator(
            "form [role='alert'], [aria-invalid='true'], .field-error, .error-message"
        )
        try:
            count = min(locators.count(), 50)
        except Exception:
            continue
        for index in range(count):
            locator = locators.nth(index)
            try:
                if not locator.is_visible():
                    continue
                message = locator.inner_text().strip() or locator.get_attribute("aria-label") or ""
            except Exception:
                continue
            if message and message not in errors:
                errors.append(message[:500])
    return errors


# ---------------------------------------------------------------------------
# Field-record consolidation
# ---------------------------------------------------------------------------
#
# A field may be observed more than once during a single execution:
#   - the initial pass records it as ``intervention_needed`` (no mapping);
#   - the LLM pass later resolves it and updates the record to ``filled``;
#   - the re-observation pass may re-extract the same field.
#
# Before the final report is returned to the runner (and before
# interventions are persisted), ``consolidate_fields`` collapses the
# list to ONE terminal record per logical field, keyed by ``field_token``.
#
# Supersession rules (later wins, in priority order):
#   1. ``filled`` supersedes ``intervention_needed`` / ``failed`` / ``skipped``
#      (the field was successfully answered after the first attempt).
#   2. ``intervention_needed`` supersedes ``skipped`` / ``blocked`` for
#      required fields (an unresolved required field is the terminal state).
#   3. ``failed`` is preserved only if no later record exists for the same
#      token (a later record means the failure was retried and superseded).
#   4. ``skipped`` / ``blocked`` are kept only if no later record exists.
#
# Records with no ``field_token`` (legacy/edge case) are passed through
# untouched — they cannot be consolidated by identity.
#
# The order of records in the final list is the order of first appearance
# (stable: the first time we saw the field), so the report's field order
# matches the DOM order on the initial observation.

# Status priority for terminal records (higher number = more terminal).
# A later record with a higher-priority status supersedes an earlier one.
_STATUS_PRIORITY: dict[str, int] = {
    "skipped": 1,
    "blocked": 2,
    "failed": 3,
    "intervention_needed": 4,
    "filled": 5,
}


def consolidate_fields(records: list[LiveFieldRecord]) -> list[LiveFieldRecord]:
    """Collapse a list of field records to one terminal record per token.

    See the module-level comment for the supersession rules. Records
    without a ``field_token`` are passed through unchanged.
    """
    if not records:
        return []

    consolidated: list[LiveFieldRecord] = []
    seen_tokens: dict[str, int] = {}  # token -> index in `consolidated`

    for record in records:
        token = record.field_token
        if not token:
            # No stable identity — cannot consolidate, pass through.
            consolidated.append(record)
            continue

        if token not in seen_tokens:
            seen_tokens[token] = len(consolidated)
            consolidated.append(record)
            continue

        # A previous record exists for this token. Apply supersession rules.
        prev_index = seen_tokens[token]
        prev = consolidated[prev_index]
        prev_prio = _STATUS_PRIORITY.get(prev.status, 0)
        new_prio = _STATUS_PRIORITY.get(record.status, 0)

        # Later record wins if its status is strictly more terminal OR
        # if it is at the same priority (a re-fill updates the record
        # with the latest evidence/explanation).
        if new_prio >= prev_prio:
            consolidated[prev_index] = record

    return consolidated


def execute_live_form(
    page: Page,
    candidate: CandidateProfile,
    job: ApplicationJob,
) -> LiveFormExecution:
    """Fill the current rendered form page and upload known documents.

    After filling radio/select/checkbox fields that may trigger conditional
    field revelation (via JavaScript change handlers), the executor re-observes
    the page to detect newly visible fields. This handles conditional questions
    that appear only after a parent answer is selected.
    """
    targets = _extract_live_fields(page)
    target_by_token = {target.token: target for target in targets}
    summary = fill_form([target.field for target in targets], candidate, job)
    execution = LiveFormExecution()
    filled_tokens: set[str] = set()

    for result in summary.results:
        target = target_by_token[result.field_selector]
        status = result.status
        explanation = result.explanation
        filled_value = ""
        actual_selected = target.field.current_value  # DOM selection before fill
        if status == "filled" and result.value is not None:
            # Validate the typed answer BEFORE Playwright touches the field.
            # Invalid answers become intervention_needed (never ``failed``)
            # so a safely-unresolved question is not misclassified.
            is_valid, reason = validate_typed_answer(
                target.field.type, result.value, target.field.options
            )
            if not is_valid:
                status = "intervention_needed"
                explanation = f"typed-answer validation failed: {reason}"
                logger.info(
                    "[%s] rejected typed answer for %s (%s): %s",
                    job.application_id[:12],
                    target.selector_hint,
                    target.field.type,
                    reason,
                )
            else:
                try:
                    # _execute_field returns the ACTUAL option that was
                    # selected/checked in the DOM (e.g. "ja" when the
                    # proposed value was "Yes"). Record that as both
                    # filled_value and selected_value so the report and
                    # persisted interventions reflect what the form
                    # actually received, not the normalized alias.
                    actual_selected = _execute_field(target, result.value)
                    filled_value = actual_selected
                    if target.field.type == "file":
                        page.wait_for_timeout(1_000)
                    elif target.field.type in ("radio", "select", "checkbox"):
                        # Radio/select/checkbox changes may trigger JavaScript
                        # that reveals conditional fields. Wait briefly for the
                        # DOM to update.
                        page.wait_for_timeout(500)
                    execution.filled += 1
                    filled_tokens.add(target.token)
                except Exception as exc:
                    status = "failed"
                    explanation = f"Playwright fill failed: {exc}"
                    logger.warning(
                        "[%s] fill failed selector=%s: %s",
                        job.application_id[:12],
                        target.selector_hint,
                        exc,
                    )

            if target.field.type == "file":
                path = Path(result.value)
                upload_status = "uploaded" if status == "filled" else "failed"
                execution.uploads.append(
                    LiveUploadRecord(
                        page_url=page.url,
                        selector=target.selector_hint,
                        document_kind=cast(Any, _document_kind(target)),
                        path=str(path),
                        status=cast(Any, upload_status),
                        message=explanation,
                    )
                )

        if target.field.required and status in {"blocked", "intervention_needed", "failed"}:
            execution.required_unresolved += 1
        execution.fields.append(
            LiveFieldRecord(
                page_url=page.url,
                selector=target.selector_hint,
                label=target.field.label,
                field_type=target.field.type,
                status=cast(Any, status),
                source=result.source,
                explanation=explanation,
                field_token=target.token,
                options=[opt.label or opt.value for opt in target.field.options],
                selected_value=actual_selected,
                filled_value=filled_value,
            )
        )

    # Re-observe the page after filling to detect newly revealed
    # conditional fields (e.g., a text input that appears only after
    # selecting "Yes" on a radio question).
    #
    # Bounded: this is a SINGLE re-observation pass, not a loop. It
    # processes only fields that were NOT in the initial extraction.
    # It does not recursively re-observe after filling revealed fields.
    # This prevents infinite loops and avoids re-filling unchanged fields.
    _MAX_REOBSERVE_PASSES = 1
    for _pass in range(_MAX_REOBSERVE_PASSES):
        if not filled_tokens:
            break
        new_targets = _extract_live_fields(page)
        existing_tokens = {f.field_token for f in execution.fields if f.field_token}
        revealed_targets = [
            t
            for t in new_targets
            if t.token not in existing_tokens and t.token not in filled_tokens
        ]
        if not revealed_targets:
            break
        # Process the newly revealed fields with the fill engine.
        revealed_fields = [t.field for t in revealed_targets]
        revealed_summary = fill_form(revealed_fields, candidate, job)
        revealed_by_token = {t.token: t for t in revealed_targets}

        for result in revealed_summary.results:
            target = revealed_by_token.get(result.field_selector)
            if target is None:
                continue
            status = result.status
            explanation = result.explanation
            filled_value = ""
            actual_selected = target.field.current_value
            if status == "filled" and result.value is not None:
                # Validate typed answer BEFORE Playwright filling. Same rule
                # as the initial pass: invalid -> intervention_needed (not failed).
                is_valid, reason = validate_typed_answer(
                    target.field.type, result.value, target.field.options
                )
                if not is_valid:
                    status = "intervention_needed"
                    explanation = f"typed-answer validation failed: {reason}"
                else:
                    try:
                        actual_selected = _execute_field(target, result.value)
                        filled_value = actual_selected
                        execution.filled += 1
                    except Exception as exc:
                        status = "failed"
                        explanation = f"Playwright fill failed: {exc}"

            if target.field.required and status in {"blocked", "intervention_needed", "failed"}:
                execution.required_unresolved += 1
            execution.fields.append(
                LiveFieldRecord(
                    page_url=page.url,
                    selector=target.selector_hint,
                    label=target.field.label,
                    field_type=target.field.type,
                    status=cast(Any, status),
                    source=result.source,
                    explanation=explanation,
                    field_token=target.token,
                    options=[opt.label or opt.value for opt in target.field.options],
                    selected_value=actual_selected,
                    filled_value=filled_value,
                )
            )
        # Only newly filled tokens from this pass could trigger another
        # reveal, but we stop here (bounded to 1 pass).

    # Consolidate duplicate records by stable field_token. A field may
    # appear in both the initial pass and the re-observation pass (e.g. a
    # radio group that was already extracted, then re-extracted after a
    # conditional reveal). The final report must contain ONE terminal
    # record per logical field.
    execution.fields = consolidate_fields(execution.fields)
    execution.validation_errors = _validation_errors(page)
    return execution


def execute_live_form_with_llm(
    page: Page,
    candidate: CandidateProfile,
    job: ApplicationJob,
    qa_service: Any = None,
    answer_memory_facts: list[Any] | None = None,
) -> LiveFormExecution:
    """Fill the current rendered form page with deterministic + LLM answers.

    This extends :func:`execute_live_form` with grounded LLM question
    resolution. For each field that deterministic mapping cannot resolve,
    the LLM resolver (:mod:`universal_auto_applier.llm.question_resolver`)
    is invoked. If the LLM proposes an answer that passes validation, it
    is filled. Otherwise, the field is recorded as requiring an
    intervention.

    Safety:
    - Deterministic mapping is tried first (never bypassed).
    - HIGH-risk categories (salary, legal, demographic, consent) are
      never auto-filled by the LLM; they always create interventions.
    - The LLM may only use candidate evidence; it must not invent facts.
    - Final submission is never triggered.
    - If the LLM service is not configured, unresolved fields become
      interventions (the pipeline does not crash).

    Stable field identity:
    - Each field has a ``field_token`` (e.g. ``live-field-0-3``) that is
      assigned during DOM extraction and propagated through
      deterministic execution → unresolved result → LLM resolution →
      Playwright fill. This ensures two similar fields cannot receive
      each other's answers.

    Args:
        page: The Playwright page with a rendered form.
        candidate: The resolved candidate profile.
        job: The application job.
        qa_service: Optional :class:`QuestionAnsweringService`. If None,
            a default is created from environment config.
        answer_memory_facts: Optional reusable approved answers.

    Returns:
        A :class:`LiveFormExecution` with all field outcomes.
    """
    # First, run the deterministic fill (existing behavior).
    execution = execute_live_form(page, candidate, job)

    # If there are no unresolved required fields, we're done.
    if execution.required_unresolved == 0:
        return execution

    # Re-extract live fields to get the locators (same extraction as
    # execute_live_form, so tokens match).
    targets = _extract_live_fields(page)
    target_by_token: dict[str, _LiveFieldTarget] = {t.token: t for t in targets}

    # Build a set of field tokens that need LLM resolution.
    # Uses the stable field_token propagated from execute_live_form.
    unresolved_tokens: set[str] = set()
    for record in execution.fields:
        if record.status in ("blocked", "intervention_needed", "failed"):
            if record.field_token:
                unresolved_tokens.add(record.field_token)

    if not unresolved_tokens:
        return execution

    # Import here to avoid circular imports at module load time.
    from universal_auto_applier.llm.qa_service import create_qa_service
    from universal_auto_applier.llm.question_resolver import resolve_question

    service = qa_service or create_qa_service()

    # Process each unresolved field using stable token matching.
    for token in unresolved_tokens:
        target = target_by_token.get(token)
        if target is None:
            # Token not found in re-extracted targets (page may have
            # changed). Leave as intervention_needed.
            continue

        # Resolve the question.
        resolution = resolve_question(
            target.field,
            candidate,
            job,
            qa_service=service,
            answer_memory_facts=answer_memory_facts,
        )

        # Find the existing field record by stable token and update it.
        for i, record in enumerate(execution.fields):
            if record.field_token != token:
                continue

            if resolution.can_auto_fill and resolution.proposed_answer is not None:
                proposed = resolution.proposed_answer
                fill_value = proposed.normalized_value or proposed.value
                # Validate the LLM answer against the declared field type
                # BEFORE Playwright touches the field. Invalid typed answers
                # become intervention_needed (never ``failed``).
                is_valid, reason = validate_typed_answer(
                    target.field.type, fill_value, target.field.options
                )
                if not is_valid:
                    execution.fields[i] = LiveFieldRecord(
                        page_url=record.page_url,
                        selector=record.selector,
                        label=record.label,
                        field_type=record.field_type,
                        status="intervention_needed",
                        source="llm_grounded",
                        explanation=f"LLM answer failed type validation: {reason}",
                        field_token=token,
                        proposed_answer=proposed.value,
                        confidence=proposed.confidence,
                        evidence_summary="; ".join(e.fact for e in proposed.evidence),
                        category=str(resolution.category),
                        risk_level=str(resolution.risk_level),
                        requires_confirmation=True,
                        options=[opt.label or opt.value for opt in target.field.options],
                        selected_value=target.field.current_value,
                    )
                    logger.info(
                        "[%s] rejected LLM typed answer for %s (%s): %s",
                        job.application_id[:12],
                        record.label,
                        target.field.type,
                        reason,
                    )
                else:
                    # Try to fill the field with the validated LLM answer.
                    # _execute_field returns the ACTUAL option selected in
                    # the DOM (e.g. "ja" when the LLM proposed "Yes").
                    try:
                        actual_selected = _execute_field(target, fill_value)
                        execution.fields[i] = LiveFieldRecord(
                            page_url=record.page_url,
                            selector=record.selector,
                            label=record.label,
                            field_type=record.field_type,
                            status="filled",
                            source="llm_grounded",
                            explanation=proposed.explanation,
                            field_token=token,
                            proposed_answer=proposed.value,
                            confidence=proposed.confidence,
                            evidence_summary="; ".join(e.fact for e in proposed.evidence),
                            category=str(resolution.category),
                            risk_level=str(resolution.risk_level),
                            requires_confirmation=False,
                            options=[opt.label or opt.value for opt in target.field.options],
                            selected_value=actual_selected,
                            filled_value=actual_selected,
                        )
                        execution.filled += 1
                        if target.field.required:
                            execution.required_unresolved = max(
                                0, execution.required_unresolved - 1
                            )
                    except Exception as exc:
                        execution.fields[i] = LiveFieldRecord(
                            page_url=record.page_url,
                            selector=record.selector,
                            label=record.label,
                            field_type=record.field_type,
                            status="failed",
                            source="llm_grounded",
                            explanation=f"LLM answer fill failed: {exc}",
                            field_token=token,
                            proposed_answer=proposed.value,
                            confidence=proposed.confidence,
                            category=str(resolution.category),
                            risk_level=str(resolution.risk_level),
                            requires_confirmation=True,
                            options=[opt.label or opt.value for opt in target.field.options],
                            selected_value=target.field.current_value,
                        )
                        logger.warning(
                            "[%s] LLM fill failed for %s: %s",
                            job.application_id[:12],
                            record.label,
                            exc,
                        )
            else:
                # LLM could not resolve — keep as intervention_needed with
                # LLM metadata for the dashboard.
                reason = resolution.refusal or resolution.unresolved_reason or "unresolved"
                proposed = resolution.proposed_answer
                execution.fields[i] = LiveFieldRecord(
                    page_url=record.page_url,
                    selector=record.selector,
                    label=record.label,
                    field_type=record.field_type,
                    status="intervention_needed",
                    source="llm_grounded" if proposed else None,
                    explanation=f"LLM unresolved: {reason}",
                    field_token=token,
                    proposed_answer=proposed.value if proposed else None,
                    confidence=proposed.confidence if proposed else None,
                    evidence_summary=(
                        "; ".join(e.fact for e in proposed.evidence) if proposed else ""
                    ),
                    category=str(resolution.category),
                    risk_level=str(resolution.risk_level),
                    requires_confirmation=True,
                    options=[opt.label or opt.value for opt in target.field.options],
                    selected_value=target.field.current_value,
                )
            break

    # Consolidate duplicate records by stable field_token. The LLM pass
    # above updates records in place (via execution.fields[i] = ...), but
    # a field that was extracted in the initial pass AND re-extracted in
    # the re-observation pass may have two records. Collapse to ONE
    # terminal record per logical field before returning to the runner.
    execution.fields = consolidate_fields(execution.fields)
    # Re-check validation errors after LLM fills.
    execution.validation_errors = _validation_errors(page)
    return execution


__all__ = [
    "LiveFormExecution",
    "execute_live_form",
    "execute_live_form_with_llm",
    "validate_typed_answer",
    "compute_field_token",
    "consolidate_fields",
]
