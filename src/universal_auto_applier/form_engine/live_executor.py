# pyright: reportOptionalIterable=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
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
import mimetypes
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from playwright.sync_api import Locator, Page

from universal_auto_applier.browser.live_models import (
    LiveFieldRecord,
    LiveUploadContract,
    LiveUploadEvidenceSource,
    LiveUploadRecord,
    LiveUploadStatus,
)
from universal_auto_applier.core.models import (
    ApplicationJob,
    CandidateProfile,
    FieldOption,
    FormField,
)
from universal_auto_applier.form_engine.fill_engine import fill_form
from universal_auto_applier.synthetic_profile import (
    SyntheticMutationProfile,
    sha256_file,
)

if TYPE_CHECKING:
    from universal_auto_applier.browser.mutation_plan import (
        MutationPlan,
        MutationPlanEntry,
    )

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


@dataclass(frozen=True)
class _FileUploadEvidence:
    status: LiveUploadStatus
    selected_file_names: tuple[str, ...]
    observed_constraints: dict[str, str | bool]
    evidence_source: LiveUploadEvidenceSource
    evidence_detail: str
    message: str
    upload_contract: LiveUploadContract | None = None

    @property
    def readiness_met(self) -> bool:
        """Whether a declared flow contract makes this evidence review-ready."""
        return (
            self.status == "selection_verified" and self.upload_contract == "native_final_submit"
        ) or (self.status == "remote_accepted" and self.upload_contract == "declared_async_status")


@dataclass(frozen=True)
class NativeFinalSubmitUploadContract:
    """UAA-owned declaration that this input is included on final form submit.

    Native browser selection alone does not establish that the application
    flow sends that file with its final request. Callers may provide this
    declaration only for a specifically qualified flow.
    """

    file_input_selector: str

    def __post_init__(self) -> None:
        if not self.file_input_selector.strip():
            raise ValueError("file_input_selector must not be empty")


@dataclass(frozen=True)
class AsyncUploadProtocol:
    """UAA-owned declaration of an observable async upload-status signal.

    A site cannot opt itself into this protocol. A caller must explicitly
    provide the file input and status selectors before remote acceptance is
    reported. The live runner does not declare any ATS protocol by default.
    """

    file_input_selector: str
    status_selector: str
    status_attribute: str = "data-upload-status"
    accepted_value: str = "accepted"
    rejected_value: str = "rejected"
    timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        if not self.file_input_selector.strip():
            raise ValueError("file_input_selector must not be empty")
        if not self.status_selector.strip():
            raise ValueError("status_selector must not be empty")
        if not self.status_attribute.strip():
            raise ValueError("status_attribute must not be empty")
        accepted = self.accepted_value.strip().casefold()
        rejected = self.rejected_value.strip().casefold()
        if not accepted or not rejected:
            raise ValueError("accepted_value and rejected_value must not be empty")
        if accepted == rejected:
            raise ValueError("accepted_value and rejected_value must be distinct")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")


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


class _FieldReadbackMismatch(ValueError):
    """A text control did not retain a stable, compatible value after blur."""

    def __init__(self, observed_value: str, reason: str) -> None:
        super().__init__(reason)
        self.observed_value = observed_value


def _date_value(value: str) -> datetime | None:
    for date_format in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), date_format)
        except ValueError:
            continue
    return None


def _text_values_compatible(field_type: str, proposed: str, observed: str) -> bool:
    """Allow only known semantic normalizations of a proposed text value."""
    if proposed == observed:
        return True
    if field_type == "email":
        return proposed.strip().casefold() == observed.strip().casefold()
    if field_type == "number":
        try:
            proposed_number = Decimal(proposed.strip())
            observed_number = Decimal(observed.strip())
        except InvalidOperation:
            return False
        return (
            proposed_number.is_finite()
            and observed_number.is_finite()
            and proposed_number == observed_number
        )
    if field_type == "date":
        proposed_date = _date_value(proposed)
        observed_date = _date_value(observed)
        return proposed_date is not None and proposed_date == observed_date
    return False


def _read_back_text_value(target: _LiveFieldTarget, proposed: str) -> str:
    """Blur a filled text control, wait for a stable DOM value, and verify it."""
    target.locator.fill(proposed)
    result = target.locator.evaluate(
        """async (el) => {
          el.blur();
          let value = String(el.value ?? '');
          let stableSince = performance.now();
          const started = stableSince;
          const stableForMs = 150;
          const timeoutMs = 1500;
          while (performance.now() - started < timeoutMs) {
            await new Promise(resolve => setTimeout(resolve, 25));
            if (!el.isConnected) {
              return { value: String(el.value ?? ''), stable: false };
            }
            const current = String(el.value ?? '');
            if (current !== value) {
              value = current;
              stableSince = performance.now();
            } else if (performance.now() - stableSince >= stableForMs) {
              return { value, stable: true };
            }
          }
          return { value: String(el.value ?? ''), stable: false };
        }"""
    )
    if not isinstance(result, dict):
        raise _FieldReadbackMismatch("", "the browser returned no text-field read-back")

    observed = str(result.get("value") or "")
    if result.get("stable") is not True:
        raise _FieldReadbackMismatch(
            observed,
            "the DOM value did not remain stable after the field lost focus",
        )

    try:
        current = target.locator.input_value()
    except Exception as exc:
        raise _FieldReadbackMismatch(
            observed,
            "the field could not be read back after it lost focus",
        ) from exc
    if current != observed:
        raise _FieldReadbackMismatch(
            current,
            "the DOM value changed after its stable read-back",
        )
    if not _text_values_compatible(target.field.type, proposed, current):
        raise _FieldReadbackMismatch(
            current,
            "the site cleared or rewrote the value incompatibly",
        )
    return current


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
    """Fill the field with ``value`` and return its verified DOM value.

    Text-like fields are read back after blur and a bounded stability check.
    Incompatible site-side changes raise :class:`_FieldReadbackMismatch`;
    compatible browser normalizations return the actual observed value.

    For select/radio/checkbox fields, the returned value is the LABEL or
    VALUE of the option that was actually selected/checked — NOT the
    proposed value. This is critical for cross-language forms: if the
    proposed answer was ``"Yes"`` but the form's options are
    ``["ja", "nein"]``, the actual DOM selection is ``"ja"`` and that is
    what gets recorded in ``filled_value`` and ``selected_value``.
    """
    field_type = target.field.type
    if field_type in {"text", "email", "phone", "textarea", "date", "number"}:
        return _read_back_text_value(target, value)
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
    if "transcript" in descriptor or "zeugnis" in descriptor or "transcript" in descriptor:
        return "transcript"
    # Generic application documents field (Vollständige Bewerbungsunterlagen) is ambiguous — caller
    # should provide explicit kind via bundle; fallback is unknown
    return "unknown"


def _is_multiple_file_input(locator: Locator) -> bool:
    """Check if a file input supports multiple files.

    Uses the DOM ``multiple`` property (most reliable) with a fallback to
    the ``multiple`` attribute presence. Playwright's ``set_input_files``
    requires the input to have ``multiple`` for >1 files; otherwise it
    would silently replace.
    """
    try:
        val = locator.evaluate("el => el.multiple")
        if isinstance(val, bool):
            return val
    except Exception:
        pass
    try:
        attr = locator.get_attribute("multiple")
        return attr is not None
    except Exception:
        return False


def _file_input_observation(locator: Locator) -> tuple[list[str], dict[str, str | bool]]:
    """Read the selected native filenames and constraints from the DOM."""
    raw = locator.evaluate(
        """el => ({
          names: Array.from(el.files || []).map(file => file.name),
          accept: el.getAttribute('accept') || '',
          multiple: Boolean(el.multiple),
          required: Boolean(el.required || el.getAttribute('aria-required') === 'true')
        })"""
    )
    if not isinstance(raw, dict):
        return [], {"accept": "", "multiple": False, "required": False}
    names = [str(name) for name in raw.get("names", [])]
    constraints: dict[str, str | bool] = {
        "accept": str(raw.get("accept", "")),
        "multiple": bool(raw.get("multiple", False)),
        "required": bool(raw.get("required", False)),
    }
    return names, constraints


def _safe_file_operation_error(exc: Exception) -> str:
    """Describe a file-operation failure without exposing browser exception text."""
    return f"Browser file operation failed ({type(exc).__name__}); file selection was not verified."


def _matches_accept_constraint(path: Path, accept: str) -> bool:
    """Return whether a local path matches the observed HTML ``accept`` list."""
    tokens = [token.strip().casefold() for token in accept.split(",") if token.strip()]
    if not tokens:
        return True
    mime_type, _encoding = mimetypes.guess_type(path.name)
    for token in tokens:
        if token.startswith(".") and path.suffix.casefold() == token:
            return True
        if token.endswith("/*") and mime_type and mime_type.startswith(token[:-1]):
            return True
        if mime_type and mime_type.casefold() == token:
            return True
    return False


def _initial_async_status(
    target: _LiveFieldTarget,
    protocol: AsyncUploadProtocol,
) -> tuple[str | None, str | None]:
    """Capture status only when one signal exists inside the target form/frame."""
    try:
        result = target.locator.evaluate(
            """(el, {selector, attribute}) => {
              const form = el.closest('form');
              if (!form) return {kind: 'no_form'};
              let matches;
              try {
                matches = [
                  ...(form.matches(selector) ? [form] : []),
                  ...form.querySelectorAll(selector)
                ];
              } catch (_) {
                return {kind: 'invalid_selector'};
              }
              if (matches.length > 1) return {kind: 'multiple'};
              if (matches.length === 0) return {kind: 'missing'};
              return {kind: 'found', value: matches[0].getAttribute(attribute)};
            }""",
            {"selector": protocol.status_selector, "attribute": protocol.status_attribute},
        )
        if not isinstance(result, dict):
            return None, "configured upload status signal could not be observed"
        kind = result.get("kind")
        if kind == "no_form":
            return None, "configured upload status signal has no target form to scope to"
        if kind == "invalid_selector":
            return None, "configured upload status selector is invalid"
        if kind == "multiple":
            return None, "configured upload status selector is not unique within the target form"
        if kind == "missing":
            return None, None
        value = result.get("value")
        return (str(value).strip().casefold() if value is not None else None), None
    except Exception:
        return None, "configured upload status signal could not be observed"


def _wait_for_declared_async_status(
    target: _LiveFieldTarget,
    protocol: AsyncUploadProtocol,
    initial_status: str | None,
) -> tuple[str, str]:
    """Wait for one post-selection terminal status scoped to the target form/frame."""
    try:
        result = target.locator.evaluate(
            """async (el, {selector, attribute, accepted, rejected, previous, timeoutMs}) => {
              const deadline = Date.now() + timeoutMs;
              while (Date.now() <= deadline) {
                const form = el.closest('form');
                if (!form) return {kind: 'no_form'};
                let matches;
                try {
                  matches = [
                    ...(form.matches(selector) ? [form] : []),
                    ...form.querySelectorAll(selector)
                  ];
                } catch (_) {
                  return {kind: 'invalid_selector'};
                }
                if (matches.length > 1) return {kind: 'multiple'};
                if (matches.length === 1) {
                  const value = (matches[0].getAttribute(attribute) || '').trim().toLowerCase();
                  if ((value === accepted || value === rejected) && value !== previous) {
                    return {kind: 'terminal', value};
                  }
                }
                await new Promise(resolve => setTimeout(resolve, 25));
              }
              return {kind: 'timeout'};
            }""",
            {
                "selector": protocol.status_selector,
                "attribute": protocol.status_attribute,
                "accepted": protocol.accepted_value.strip().casefold(),
                "rejected": protocol.rejected_value.strip().casefold(),
                "previous": initial_status,
                "timeoutMs": protocol.timeout_ms,
            },
        )
        if not isinstance(result, dict):
            return "unknown", "Configured upload status signal could not be observed."
        kind = result.get("kind")
        if kind == "multiple":
            return (
                "unknown",
                "Configured upload status selector is not unique within the target form.",
            )
        if kind == "no_form":
            return "unknown", "Configured upload status signal lost its target form."
        if kind == "invalid_selector":
            return "unknown", "Configured upload status selector is invalid."
        if kind == "timeout":
            return "unknown", (
                "No terminal upload status was observed from the configured target-form signal "
                f"within {protocol.timeout_ms} ms."
            )
        normalized = str(result.get("value", "")).strip().casefold()
        if normalized == protocol.accepted_value.strip().casefold():
            return "remote_accepted", (
                "Observed the declared target-form upload signal report acceptance after file selection."
            )
        if normalized == protocol.rejected_value.strip().casefold():
            return "rejected", (
                "Observed the declared target-form upload signal report rejection after file selection."
            )
        return "unknown", "Configured upload status changed to an unrecognized value."
    except Exception:
        return "unknown", (
            "No terminal upload status was observed from the configured target-form signal "
            f"within {protocol.timeout_ms} ms."
        )


def _protocol_for_target(
    target: _LiveFieldTarget,
    protocols: Sequence[AsyncUploadProtocol],
) -> tuple[AsyncUploadProtocol | None, bool]:
    matches: list[AsyncUploadProtocol] = []
    for protocol in protocols:
        try:
            if target.locator.evaluate(
                "(el, selector) => el.matches(selector)", protocol.file_input_selector
            ):
                matches.append(protocol)
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _async_status_correlation_error(
    target: _LiveFieldTarget,
    paths: list[str],
) -> str | None:
    """Require enough form context to tie one async status to one selection."""
    if len(paths) > 1:
        return (
            "The async upload signal cannot be attributed to every file in this multi-file "
            "bundle without an explicit aggregate-status contract."
        )
    try:
        input_count = int(
            target.locator.evaluate(
                """(el) => {
                  const form = el.closest('form');
                  return form ? form.querySelectorAll('input[type="file"]').length : 0;
                }"""
            )
        )
    except Exception:
        return "The target form's file inputs could not be checked to attribute async status."
    if input_count != 1:
        return (
            "The async upload signal cannot be attributed when the target form contains "
            "multiple file inputs."
        )
    return None


def _native_contract_for_target(
    target: _LiveFieldTarget,
    contracts: Sequence[NativeFinalSubmitUploadContract],
) -> tuple[NativeFinalSubmitUploadContract | None, bool]:
    matches: list[NativeFinalSubmitUploadContract] = []
    for contract in contracts:
        try:
            if target.locator.evaluate(
                "(el, selector) => el.matches(selector)", contract.file_input_selector
            ):
                matches.append(contract)
        except Exception:
            continue
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _select_files_with_evidence(
    *,
    target: _LiveFieldTarget,
    paths: list[str],
    async_upload_protocols: Sequence[AsyncUploadProtocol] = (),
    native_upload_contracts: Sequence[NativeFinalSubmitUploadContract] = (),
) -> _FileUploadEvidence:
    """Select local files, verify the native control, and optionally observe a declared signal.

    Native selection proves only that the rendered input holds the expected
    filenames and accepts their types. It is review-ready only when the UAA
    caller supplies a matching :class:`NativeFinalSubmitUploadContract`.
    Remote acceptance is reported only when the caller supplies a matching
    :class:`AsyncUploadProtocol`.
    """
    protocol, ambiguous_protocol = _protocol_for_target(target, async_upload_protocols)
    native_contract, ambiguous_native_contract = _native_contract_for_target(
        target, native_upload_contracts
    )
    if ambiguous_protocol or ambiguous_native_contract or (protocol and native_contract):
        contract_error = True
    else:
        contract_error = False
    upload_contract = (
        "declared_async_status"
        if protocol is not None and not contract_error
        else ("native_final_submit" if native_contract is not None and not contract_error else None)
    )
    initial_status: str | None = None
    protocol_error: str | None = None
    if protocol is not None:
        protocol_error = _async_status_correlation_error(target, paths)
        if protocol_error is None:
            initial_status, protocol_error = _initial_async_status(target, protocol)

    try:
        target.locator.set_input_files(paths)
        selected_file_names, constraints = _file_input_observation(target.locator)
    except Exception as exc:
        try:
            selected_file_names, constraints = _file_input_observation(target.locator)
        except Exception:
            selected_file_names, constraints = (
                [],
                {
                    "accept": "",
                    "multiple": False,
                    "required": False,
                },
            )
        return _FileUploadEvidence(
            status="failed",
            selected_file_names=tuple(selected_file_names),
            observed_constraints=constraints,
            evidence_source="unknown",
            evidence_detail="Native file selection failed before it could be verified.",
            message=_safe_file_operation_error(exc),
            upload_contract=upload_contract,
        )

    expected_names = [Path(path).name for path in paths]
    if selected_file_names != expected_names:
        return _FileUploadEvidence(
            status="unknown",
            selected_file_names=tuple(selected_file_names),
            observed_constraints=constraints,
            evidence_source="native_selection",
            evidence_detail=(
                f"Expected selected filenames {expected_names!r}; observed {selected_file_names!r}."
            ),
            message="The native file input did not expose the expected selected filenames.",
            upload_contract=upload_contract,
        )

    accept = str(constraints.get("accept", ""))
    mismatched = [path for path in paths if not _matches_accept_constraint(Path(path), accept)]
    if mismatched:
        return _FileUploadEvidence(
            status="unknown",
            selected_file_names=tuple(selected_file_names),
            observed_constraints=constraints,
            evidence_source="input_constraint",
            evidence_detail=(
                f"Observed local accept hint {accept!r} does not match "
                f"{[Path(path).name for path in mismatched]!r}; the site did not report rejection."
            ),
            message="The observed local file-type hint does not match the selected file.",
            upload_contract=upload_contract,
        )

    if contract_error:
        return _FileUploadEvidence(
            status="unknown",
            selected_file_names=tuple(selected_file_names),
            observed_constraints=constraints,
            evidence_source="unknown",
            evidence_detail=("Upload flow declarations are ambiguous for this file input."),
            message="Upload readiness is ambiguous because multiple or conflicting flow declarations matched.",
        )

    if protocol is not None:
        if protocol_error is not None:
            return _FileUploadEvidence(
                status="unknown",
                selected_file_names=tuple(selected_file_names),
                observed_constraints=constraints,
                evidence_source="declared_site_status",
                evidence_detail=protocol_error,
                message="Native selection is verified, but the configured site status signal was unavailable.",
                upload_contract=upload_contract,
            )
        status, detail = _wait_for_declared_async_status(target, protocol, initial_status)
        if status == "remote_accepted":
            return _FileUploadEvidence(
                status="remote_accepted",
                selected_file_names=tuple(selected_file_names),
                observed_constraints=constraints,
                evidence_source="declared_site_status",
                evidence_detail=detail,
                message="The configured site signal explicitly reported acceptance.",
                upload_contract=upload_contract,
            )
        if status == "rejected":
            return _FileUploadEvidence(
                status="rejected",
                selected_file_names=tuple(selected_file_names),
                observed_constraints=constraints,
                evidence_source="declared_site_status",
                evidence_detail=detail,
                message="The configured site signal explicitly reported rejection.",
                upload_contract=upload_contract,
            )
        return _FileUploadEvidence(
            status="unknown",
            selected_file_names=tuple(selected_file_names),
            observed_constraints=constraints,
            evidence_source="declared_site_status",
            evidence_detail=detail,
            message="Native file selection is verified, but remote acceptance remains unknown.",
            upload_contract=upload_contract,
        )

    native_readiness_text = (
        "The qualified flow declares this native file input is included in the final submit."
        if upload_contract == "native_final_submit"
        else "No native final-submit flow contract was declared; selection alone does not establish upload readiness."
    )
    return _FileUploadEvidence(
        status="selection_verified",
        selected_file_names=tuple(selected_file_names),
        observed_constraints=constraints,
        evidence_source="native_selection",
        evidence_detail=(
            "Observed the expected filenames in the native file input and checked its "
            f"accept constraint. {native_readiness_text}"
        ),
        message=(
            "Native file selection verified under a qualified final-submit flow."
            if upload_contract == "native_final_submit"
            else "Native file selection is visible, but the flow is not qualified for review readiness."
        ),
        upload_contract=upload_contract,
    )


def _upload_record(
    *,
    page: Page,
    target: _LiveFieldTarget,
    path: str,
    document_kind: str,
    evidence: _FileUploadEvidence,
    selected_file_names: Sequence[str] | None = None,
) -> LiveUploadRecord:
    return LiveUploadRecord(
        page_url=page.url,
        selector=target.selector_hint,
        document_kind=cast(Any, document_kind),
        path=path,
        status=evidence.status,
        selected_file_names=list(
            evidence.selected_file_names if selected_file_names is None else selected_file_names
        ),
        observed_constraints=evidence.observed_constraints,
        evidence_source=evidence.evidence_source,
        upload_contract=evidence.upload_contract,
        evidence_detail=evidence.evidence_detail,
        message=evidence.message,
    )


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
    seen_tokens: dict[tuple[str, str], int] = {}  # step/token -> index in `consolidated`

    for record in records:
        token = record.field_token
        if not token:
            # No stable identity — cannot consolidate, pass through.
            consolidated.append(record)
            continue
        identity = (record.step_identity, token)

        if identity not in seen_tokens:
            seen_tokens[identity] = len(consolidated)
            consolidated.append(record)
            continue

        # A previous record exists for this same-step token. Apply supersession rules.
        prev_index = seen_tokens[identity]
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
    async_upload_protocols: Sequence[AsyncUploadProtocol] = (),
    native_upload_contracts: Sequence[NativeFinalSubmitUploadContract] = (),
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
        proposed_answer: str | None = None
        # Structured file-bundle handling: if this is a FILE field with an
        # owner-selected document bundle (e.g. Vollständige Bewerbungsunterlagen
        # with CV + transcript), handle it before scalar validation. The bundle
        # is an ordered list of (path, kind) preserving the owner's selection.
        # For scalar text/select/radio fields the bundle is always None.
        is_file_bundle = (
            target.field.type == "file"
            and result.document_bundle is not None
            and len(result.document_bundle) > 0
        )
        bundle_upload_handled = False
        if is_file_bundle and status == "filled" and result.value is not None:
            assert result.document_bundle is not None
            bundle = result.document_bundle
            # Validate typed answer for the first path (scalar check) is not
            # sufficient for bundles — the fill_engine already validated every
            # path, but we re-validate the bundle shape before touching the
            # browser. For bundles we bypass validate_typed_answer's single-value
            # check and validate the bundle directly.
            # Check multiple capability before any upload
            paths = [entry.path for entry in bundle]
            # Validate every selected path exists, is file, not directory
            bundle_valid = True
            for entry in bundle:
                p = Path(entry.path)
                if not p.exists():
                    status = "intervention_needed"
                    explanation = f"File in bundle does not exist: {entry.path}"
                    bundle_valid = False
                    break
                if not p.is_file():
                    status = "intervention_needed"
                    explanation = f"Bundle path is not a regular file: {entry.path}"
                    bundle_valid = False
                    break
            if bundle_valid:
                is_multiple = _is_multiple_file_input(target.locator)
                if len(paths) > 1 and not is_multiple:
                    status = "intervention_needed"
                    explanation = (
                        f"File input does not support multiple files "
                        f"(multiple=false) but bundle has {len(paths)} files"
                    )
                    bundle_valid = False
                else:
                    try:
                        evidence = _select_files_with_evidence(
                            target=target,
                            paths=paths,
                            async_upload_protocols=async_upload_protocols,
                            native_upload_contracts=native_upload_contracts,
                        )
                        filled_value = ", ".join(paths)
                        actual_selected = ", ".join(evidence.selected_file_names)
                        for index, entry in enumerate(bundle):
                            kind = entry.kind
                            if kind not in (
                                "cv",
                                "cover_letter",
                                "transcript",
                                "attachment",
                                "unknown",
                            ):
                                kind = _document_kind(target)
                            execution.uploads.append(
                                _upload_record(
                                    page=page,
                                    target=target,
                                    document_kind=cast(Any, kind),
                                    path=entry.path,
                                    evidence=evidence,
                                    selected_file_names=(
                                        [evidence.selected_file_names[index]]
                                        if index < len(evidence.selected_file_names)
                                        else []
                                    ),
                                )
                            )
                        if evidence.readiness_met:
                            execution.filled += 1
                            filled_tokens.add(target.token)
                        else:
                            status = (
                                "failed" if evidence.status == "failed" else "intervention_needed"
                            )
                            explanation = evidence.message
                        bundle_upload_handled = True
                    except Exception as exc:
                        status = "failed"
                        explanation = _safe_file_operation_error(exc)
                        logger.warning(
                            "[%s] fill failed selector=%s: %s",
                            job.application_id[:12],
                            target.selector_hint,
                            exc,
                        )
            # If bundle was rejected (not multiple, missing file, etc.),
            # status is now intervention_needed and we must NOT create
            # uploaded records — the field will be recorded as requiring
            # intervention and the snapshot will not falsely claim the
            # complete package.
            if not bundle_valid:
                # Do not create uploaded records for failed bundle;
                # the field status already reflects intervention_needed.
                # We still need to avoid the single-file upload path below.
                bundle_upload_handled = True
            # Mark bundle handling complete (whether success or fail)
            # so the single-file upload path below is skipped.
            if bundle_upload_handled:
                # For bundle failure we already have status/explanation;
                # for success we have uploads and filled.
                pass
            else:
                # Should not reach here — fallback to scalar
                is_file_bundle = False

        if not is_file_bundle or not bundle_upload_handled:
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
                        if target.field.type == "file":
                            evidence = _select_files_with_evidence(
                                target=target,
                                paths=[result.value],
                                async_upload_protocols=async_upload_protocols,
                                native_upload_contracts=native_upload_contracts,
                            )
                            actual_selected = ", ".join(evidence.selected_file_names)
                            filled_value = result.value
                            execution.uploads.append(
                                _upload_record(
                                    page=page,
                                    target=target,
                                    document_kind=_document_kind(target),
                                    path=str(Path(result.value)),
                                    evidence=evidence,
                                )
                            )
                            if evidence.readiness_met:
                                execution.filled += 1
                                filled_tokens.add(target.token)
                            else:
                                status = (
                                    "failed"
                                    if evidence.status == "failed"
                                    else "intervention_needed"
                                )
                                explanation = evidence.message
                        else:
                            # _execute_field returns the actual DOM selection,
                            # not an alias proposed by the fill engine.
                            actual_selected = _execute_field(target, result.value)
                            filled_value = actual_selected
                            execution.filled += 1
                            filled_tokens.add(target.token)
                        if target.field.type in ("radio", "select", "checkbox"):
                            # Radio/select/checkbox changes may trigger JavaScript
                            # that reveals conditional fields. Wait briefly for the
                            # DOM to update.
                            page.wait_for_timeout(500)
                    except Exception as exc:
                        if isinstance(exc, _FieldReadbackMismatch):
                            status = "intervention_needed"
                            actual_selected = exc.observed_value
                            proposed_answer = result.value
                            explanation = f"Text-field read-back validation failed: {exc}"
                            execution.validation_errors.append(explanation)
                        else:
                            status = "failed"
                            explanation = (
                                _safe_file_operation_error(exc)
                                if target.field.type == "file"
                                else f"Playwright fill failed: {exc}"
                            )
                        logger.warning(
                            "[%s] fill failed selector=%s: %s",
                            job.application_id[:12],
                            target.selector_hint,
                            exc,
                        )

        if status in {"blocked", "intervention_needed", "failed"} and (
            target.field.required
            or (target.field.type == "file" and (result.value is not None or is_file_bundle))
        ):
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
                proposed_answer=proposed_answer,
                options=[opt.label or opt.value for opt in target.field.options],
                required=target.field.required,
                selected_value=actual_selected,
                filled_value=filled_value,
            )
        )

    # Re-observe the page after filling to detect newly revealed
    # conditional fields (e.g., a text input that appears only after
    # selecting "Yes" on a radio question).
    #
    # Bounded: each pass processes only fields not previously extracted.
    # Re-observation continues after newly filled fields so nested conditional
    # questions can be resolved, with a fixed cap to prevent runaway pages.
    _MAX_REOBSERVE_PASSES = 5
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
            proposed_answer: str | None = None
            # Bundle handling for revealed file fields (same as initial pass)
            is_file_bundle_revealed = (
                target.field.type == "file"
                and result.document_bundle is not None
                and len(result.document_bundle) > 0
            )
            if is_file_bundle_revealed and status == "filled" and result.value is not None:
                assert result.document_bundle is not None
                bundle = result.document_bundle
                paths = [e.path for e in bundle]
                bundle_valid = True
                for entry in bundle:
                    p = Path(entry.path)
                    if not p.exists() or not p.is_file():
                        status = "intervention_needed"
                        explanation = f"Bundle file missing or not a file: {entry.path}"
                        bundle_valid = False
                        break
                if bundle_valid:
                    is_multiple = _is_multiple_file_input(target.locator)
                    if len(paths) > 1 and not is_multiple:
                        status = "intervention_needed"
                        explanation = (
                            f"File input does not support multiple files "
                            f"but bundle has {len(paths)} files"
                        )
                    else:
                        try:
                            evidence = _select_files_with_evidence(
                                target=target,
                                paths=paths,
                                async_upload_protocols=async_upload_protocols,
                                native_upload_contracts=native_upload_contracts,
                            )
                            filled_value = ", ".join(paths)
                            actual_selected = ", ".join(evidence.selected_file_names)
                            for index, entry in enumerate(bundle):
                                kind = entry.kind
                                if kind not in (
                                    "cv",
                                    "cover_letter",
                                    "transcript",
                                    "attachment",
                                    "unknown",
                                ):
                                    kind = _document_kind(target)
                                execution.uploads.append(
                                    _upload_record(
                                        page=page,
                                        target=target,
                                        document_kind=cast(Any, kind),
                                        path=entry.path,
                                        evidence=evidence,
                                        selected_file_names=(
                                            [evidence.selected_file_names[index]]
                                            if index < len(evidence.selected_file_names)
                                            else []
                                        ),
                                    )
                                )
                            if not evidence.readiness_met:
                                status = (
                                    "failed"
                                    if evidence.status == "failed"
                                    else "intervention_needed"
                                )
                                explanation = evidence.message
                            else:
                                execution.filled += 1
                        except Exception as exc:
                            status = "failed"
                            explanation = _safe_file_operation_error(exc)
            elif status == "filled" and result.value is not None:
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
                        if target.field.type == "file":
                            evidence = _select_files_with_evidence(
                                target=target,
                                paths=[result.value],
                                async_upload_protocols=async_upload_protocols,
                                native_upload_contracts=native_upload_contracts,
                            )
                            actual_selected = ", ".join(evidence.selected_file_names)
                            filled_value = result.value
                            execution.uploads.append(
                                _upload_record(
                                    page=page,
                                    target=target,
                                    document_kind=_document_kind(target),
                                    path=str(Path(result.value)),
                                    evidence=evidence,
                                )
                            )
                            if not evidence.readiness_met:
                                status = (
                                    "failed"
                                    if evidence.status == "failed"
                                    else "intervention_needed"
                                )
                                explanation = evidence.message
                            else:
                                execution.filled += 1
                        else:
                            actual_selected = _execute_field(target, result.value)
                            filled_value = actual_selected
                            execution.filled += 1
                    except Exception as exc:
                        if isinstance(exc, _FieldReadbackMismatch):
                            status = "intervention_needed"
                            actual_selected = exc.observed_value
                            proposed_answer = result.value
                            explanation = f"Text-field read-back validation failed: {exc}"
                            execution.validation_errors.append(explanation)
                        else:
                            status = "failed"
                            explanation = (
                                _safe_file_operation_error(exc)
                                if target.field.type == "file"
                                else f"Playwright fill failed: {exc}"
                            )
                        if target.field.type == "file":
                            execution.uploads.append(
                                _upload_record(
                                    page=page,
                                    target=target,
                                    document_kind=_document_kind(target),
                                    path=result.value or "",
                                    evidence=_FileUploadEvidence(
                                        status="failed",
                                        selected_file_names=(),
                                        observed_constraints={},
                                        evidence_source="unknown",
                                        evidence_detail=explanation,
                                        message=explanation,
                                    ),
                                )
                            )

            if status in {"blocked", "intervention_needed", "failed"} and (
                target.field.required
                or (
                    target.field.type == "file"
                    and (result.value is not None or is_file_bundle_revealed)
                )
            ):
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
                    proposed_answer=proposed_answer,
                    options=[opt.label or opt.value for opt in target.field.options],
                    required=target.field.required,
                    selected_value=actual_selected,
                    filled_value=filled_value,
                )
            )
        # Only newly filled tokens from this pass can trigger another reveal;
        # the next bounded pass checks for additional visible fields.

    # Consolidate duplicate records by stable field_token. A field may
    # appear in both the initial pass and the re-observation pass (e.g. a
    # radio group that was already extracted, then re-extracted after a
    # conditional reveal). The final report must contain ONE terminal
    # record per logical field.
    execution.fields = consolidate_fields(execution.fields)
    for error in _validation_errors(page):
        if error not in execution.validation_errors:
            execution.validation_errors.append(error)
    return execution


def execute_live_form_with_llm(
    page: Page,
    candidate: CandidateProfile,
    job: ApplicationJob,
    qa_service: Any = None,
    answer_memory_facts: list[Any] | None = None,
    async_upload_protocols: Sequence[AsyncUploadProtocol] = (),
    native_upload_contracts: Sequence[NativeFinalSubmitUploadContract] = (),
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
    execution = execute_live_form(
        page,
        candidate,
        job,
        async_upload_protocols=async_upload_protocols,
        native_upload_contracts=native_upload_contracts,
    )

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
        if record.status in ("blocked", "intervention_needed", "failed") and (
            record.field_type != "file"
        ):
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
                        required=target.field.required,
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
                            required=target.field.required,
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
                        if isinstance(exc, _FieldReadbackMismatch):
                            explanation = f"Text-field read-back validation failed: {exc}"
                            execution.validation_errors.append(explanation)
                            execution.fields[i] = LiveFieldRecord(
                                page_url=record.page_url,
                                selector=record.selector,
                                label=record.label,
                                field_type=record.field_type,
                                status="intervention_needed",
                                source="llm_grounded",
                                explanation=explanation,
                                field_token=token,
                                proposed_answer=proposed.value,
                                confidence=proposed.confidence,
                                category=str(resolution.category),
                                risk_level=str(resolution.risk_level),
                                requires_confirmation=True,
                                required=target.field.required,
                                options=[opt.label or opt.value for opt in target.field.options],
                                selected_value=exc.observed_value,
                            )
                        else:
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
                                required=target.field.required,
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
                    required=target.field.required,
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
    for error in _validation_errors(page):
        if error not in execution.validation_errors:
            execution.validation_errors.append(error)
    return execution


@dataclass
class SyntheticMutationPass:
    """One frozen mutation pass with its own pre-mutation plan.

    Every pass (the initial extraction pass and each bounded reveal pass)
    builds and freezes its own plan BEFORE mutating anything. Keeping the
    passes ordered lets evidence prove that every actual mutation belongs
    to a hash-verifiable plan.
    """

    pass_index: int
    plan: MutationPlan
    plan_hash: str
    mutations_performed: int
    budget_consumed: int


@dataclass
class SyntheticMutationExecution:
    """Result of one WQ-7C synthetic mutation pass over a live form.

    The frozen :attr:`plan` and its :attr:`plan_hash` are recorded before
    any mutation happened. Only entries the plan decided ``mutate`` were
    written; everything else was left untouched.

    :attr:`passes` is the deterministic ordered chain of every frozen plan
    that ran (initial + each reveal pass). :attr:`plan_chain_hash` covers
    that whole ordered chain so the persisted evidence proves no mutation
    happened outside a frozen plan.
    """

    plan: MutationPlan
    plan_hash: str
    mutations_performed: int
    budget_consumed: int = 0
    fields: list[LiveFieldRecord] = field(default_factory=list[LiveFieldRecord])
    uploads: list[LiveUploadRecord] = field(default_factory=list[LiveUploadRecord])
    validation_errors: list[str] = field(default_factory=list[str])
    required_unresolved: int = 0
    passes: list[SyntheticMutationPass] = field(default_factory=list[SyntheticMutationPass])
    plan_chain_hash: str = ""


def plan_chain_hash(plan_hashes: list[str]) -> str:
    """Deterministic SHA-256 over the ordered chain of per-pass plan hashes.

    The order matters (reveal runs after the initial pass), and the input is
    the per-pass plan hash — which itself excludes the volatile
    ``generated_at`` timestamp — so identical chains always re-verify.
    """
    canonical = json.dumps(
        list(plan_hashes),
        sort_keys=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _entry_to_status(entry: MutationPlanEntry) -> str:
    """Map a plan decision onto the LiveFieldRecord status vocabulary."""
    return {
        "mutate": "filled",
        "skip": "skipped",
        "block": "blocked",
        "needs_intervention": "intervention_needed",
    }[entry.decision]


def _record_for_entry(
    *,
    entry: MutationPlanEntry,
    target: _LiveFieldTarget | None,
    page: Page,
    status: str,
    filled_value: str = "",
    selected_value: str = "",
    explanation: str | None = None,
) -> LiveFieldRecord:
    """Build the evidence record for one plan entry."""
    return LiveFieldRecord(
        page_url=page.url,
        selector=target.selector_hint if target else entry.selector,
        label=entry.label,
        field_type=entry.field_type,
        status=cast(Any, status),
        source=entry.value_source or None,
        explanation=explanation if explanation is not None else entry.explanation,
        field_token=entry.selector,
        proposed_answer=entry.proposed_value,
        confidence=entry.confidence,
        category=entry.category,
        risk_level=entry.risk_level,
        requires_confirmation=False,
        required=target.field.required if target else False,
        options=entry.options,
        selected_value=selected_value,
        filled_value=filled_value,
    )


def _run_mutation_pass(
    *,
    page: Page,
    mutation_profile: SyntheticMutationProfile,
    job: ApplicationJob,
    approved_document_hashes: frozenset[str],
    budget: int,
    existing_tokens: set[str] | None = None,
    async_upload_protocols: Sequence[AsyncUploadProtocol] = (),
    native_upload_contracts: Sequence[NativeFinalSubmitUploadContract] = (),
) -> SyntheticMutationExecution:
    """Extract, plan, and execute one mutation pass (initial or revealed).

    ``existing_tokens`` filters the extraction to fields the caller has not
    already processed. The plan is built and frozen BEFORE any mutation.
    """
    from universal_auto_applier.browser.mutation_plan import build_mutation_plan

    candidate = mutation_profile.to_candidate_profile()
    targets = _extract_live_fields(page)
    if existing_tokens:
        targets = [t for t in targets if t.token not in existing_tokens]
    if not targets:
        empty_plan = build_mutation_plan(
            [],
            candidate,
            job,
            approved_document_hashes=approved_document_hashes,
            budget=budget,
            application_id=job.application_id,
            page_url=page.url,
        )
        return SyntheticMutationExecution(
            plan=empty_plan,
            plan_hash=empty_plan.plan_hash,
            mutations_performed=0,
        )

    plan = build_mutation_plan(
        [t.field for t in targets],
        candidate,
        job,
        approved_document_hashes=approved_document_hashes,
        budget=budget,
        application_id=job.application_id,
        page_url=page.url,
    )
    plan_hash = plan.plan_hash  # frozen BEFORE any mutation
    target_by_token = {t.token: t for t in targets}

    execution = SyntheticMutationExecution(
        plan=plan,
        plan_hash=plan_hash,
        mutations_performed=0,
    )
    budget_remaining = budget

    for entry in plan.entries:
        target = target_by_token.get(entry.selector)
        if entry.decision == "mutate":
            if budget_remaining <= 0:
                execution.fields.append(
                    _record_for_entry(
                        entry=entry,
                        target=target,
                        page=page,
                        status="skipped",
                        explanation="mutation budget exhausted",
                    )
                )
                continue
            if entry.proposed_value is None:
                execution.fields.append(
                    _record_for_entry(
                        entry=entry,
                        target=target,
                        page=page,
                        status="blocked",
                        explanation="mutate entry has no proposed value",
                    )
                )
                continue
            # Defense in depth: re-verify document hashes at execution time.
            if entry.value_source == "document_path" and entry.proposed_value:
                doc_path = Path(entry.proposed_value)
                digest = sha256_file(doc_path) if doc_path.is_file() else ""
                if digest not in approved_document_hashes:
                    execution.fields.append(
                        _record_for_entry(
                            entry=entry,
                            target=target,
                            page=page,
                            status="blocked",
                            explanation=(
                                "Document hash not approved at execution time; upload refused."
                            ),
                        )
                    )
                    continue
            if target is None:
                execution.fields.append(
                    _record_for_entry(
                        entry=entry,
                        target=None,
                        page=page,
                        status="failed",
                        explanation="field target not found in DOM",
                    )
                )
                continue
            is_valid, reason = validate_typed_answer(
                target.field.type, entry.proposed_value, target.field.options
            )
            if not is_valid:
                execution.fields.append(
                    _record_for_entry(
                        entry=entry,
                        target=target,
                        page=page,
                        status="intervention_needed",
                        explanation=f"typed-answer validation failed: {reason}",
                    )
                )
                continue
            if target.field.type == "file":
                evidence = _select_files_with_evidence(
                    target=target,
                    paths=[entry.proposed_value],
                    async_upload_protocols=async_upload_protocols,
                    native_upload_contracts=native_upload_contracts,
                )
                kind = _document_kind(target)
                execution.uploads.append(
                    _upload_record(
                        page=page,
                        target=target,
                        document_kind=kind,
                        path=str(entry.proposed_value),
                        evidence=evidence,
                    )
                )
                selected_value = ", ".join(evidence.selected_file_names)
                if evidence.status in {"selection_verified", "remote_accepted"}:
                    execution.mutations_performed += 1
                    execution.budget_consumed += 1
                    budget_remaining -= 1
                    execution.fields.append(
                        _record_for_entry(
                            entry=entry,
                            target=target,
                            page=page,
                            status="filled",
                            selected_value=selected_value,
                            filled_value=str(entry.proposed_value),
                        )
                    )
                else:
                    if selected_value == Path(entry.proposed_value).name:
                        execution.mutations_performed += 1
                        execution.budget_consumed += 1
                        budget_remaining -= 1
                    execution.required_unresolved += 1
                    failed_status = (
                        "failed" if evidence.status == "failed" else "intervention_needed"
                    )
                    execution.fields.append(
                        _record_for_entry(
                            entry=entry,
                            target=target,
                            page=page,
                            status=failed_status,
                            selected_value=selected_value,
                            filled_value=str(entry.proposed_value),
                            explanation=evidence.message,
                        )
                    )
                continue
            try:
                actual = _execute_field(target, entry.proposed_value)
                if target.field.type in ("radio", "select", "checkbox"):
                    page.wait_for_timeout(500)
                execution.mutations_performed += 1
                execution.budget_consumed += 1
                budget_remaining -= 1
                execution.fields.append(
                    _record_for_entry(
                        entry=entry,
                        target=target,
                        page=page,
                        status="filled",
                        selected_value=actual,
                        filled_value=actual,
                    )
                )
            except Exception as exc:
                if isinstance(exc, _FieldReadbackMismatch):
                    explanation = f"Text-field read-back validation failed: {exc}"
                    execution.validation_errors.append(explanation)
                    execution.fields.append(
                        _record_for_entry(
                            entry=entry,
                            target=target,
                            page=page,
                            status="intervention_needed",
                            selected_value=exc.observed_value,
                            explanation=explanation,
                        )
                    )
                    if target.field.required:
                        execution.required_unresolved += 1
                else:
                    execution.fields.append(
                        _record_for_entry(
                            entry=entry,
                            target=target,
                            page=page,
                            status="failed",
                            explanation=f"Playwright mutation failed: {exc}",
                        )
                    )
                logger.warning(
                    "[%s] wq7c mutation failed selector=%s: %s",
                    job.application_id[:12],
                    target.selector_hint,
                    exc,
                )
        else:
            status = _entry_to_status(entry)
            execution.fields.append(
                _record_for_entry(
                    entry=entry,
                    target=target,
                    page=page,
                    status=status,
                )
            )
            if (
                target is not None
                and target.field.required
                and status
                in {
                    "blocked",
                    "intervention_needed",
                    "failed",
                }
            ):
                execution.required_unresolved += 1

    return execution


def execute_live_form_synthetic(
    page: Page,
    mutation_profile: SyntheticMutationProfile,
    job: ApplicationJob,
    *,
    approved_document_hashes: frozenset[str],
    mutation_budget: int,
    async_upload_protocols: Sequence[AsyncUploadProtocol] = (),
    native_upload_contracts: Sequence[NativeFinalSubmitUploadContract] = (),
) -> SyntheticMutationExecution:
    """Fill the current rendered form page with WQ-7C synthetic data only.

    The mutation PLAN is built and frozen (hashed) before any field is
    touched. Only entries the plan decided ``mutate`` are executed, in
    order, consuming ``mutation_budget`` per field; everything else is
    recorded and left untouched. Document uploads are only ever performed
    for files whose SHA-256 is in ``approved_document_hashes``.

    After the initial pass, bounded re-observation passes handle newly
    revealed conditional fields the same way. Passive/non-fill inputs
    (submit buttons, hidden inputs) are never mutated, and final submission
    is never triggered here.
    """
    execution = _run_mutation_pass(
        page=page,
        mutation_profile=mutation_profile,
        job=job,
        approved_document_hashes=approved_document_hashes,
        budget=mutation_budget,
        async_upload_protocols=async_upload_protocols,
        native_upload_contracts=native_upload_contracts,
    )

    # Deterministic ordered plan chain: the initial pass is always pass 0;
    # each bounded reveal pass appends a new frozen plan. EVERY actual
    # mutation must belong to one of these hash-verifiable plans.
    passes: list[SyntheticMutationPass] = [
        SyntheticMutationPass(
            pass_index=0,
            plan=execution.plan,
            plan_hash=execution.plan_hash,
            mutations_performed=execution.mutations_performed,
            budget_consumed=execution.budget_consumed,
        )
    ]

    # Bounded re-observation for conditionally revealed fields. Every actual
    # mutation still belongs to its own frozen, hash-verifiable plan.
    remaining_budget = mutation_budget - execution.budget_consumed
    existing_tokens = {f.field_token for f in execution.fields if f.field_token}
    _MAX_SYNTHETIC_REVEAL_PASSES = 5
    for pass_index in range(1, _MAX_SYNTHETIC_REVEAL_PASSES + 1):
        if not existing_tokens or remaining_budget < 1:
            break
        revealed = _run_mutation_pass(
            page=page,
            mutation_profile=mutation_profile,
            job=job,
            approved_document_hashes=approved_document_hashes,
            budget=remaining_budget,
            existing_tokens=existing_tokens,
            async_upload_protocols=async_upload_protocols,
            native_upload_contracts=native_upload_contracts,
        )
        if not revealed.fields and not revealed.uploads:
            break
        # Merge revealed fields/records into the main execution. The
        # budget is shared: revealed mutations count against it.
        execution.mutations_performed += revealed.mutations_performed
        execution.budget_consumed += revealed.budget_consumed
        execution.fields.extend(revealed.fields)
        execution.uploads.extend(revealed.uploads)
        execution.required_unresolved += revealed.required_unresolved
        passes.append(
            SyntheticMutationPass(
                pass_index=pass_index,
                plan=revealed.plan,
                plan_hash=revealed.plan_hash,
                mutations_performed=revealed.mutations_performed,
                budget_consumed=revealed.budget_consumed,
            )
        )
        existing_tokens.update(f.field_token for f in revealed.fields if f.field_token)
        remaining_budget = mutation_budget - execution.budget_consumed
        if revealed.mutations_performed == 0:
            break

    execution.passes = passes
    execution.plan_chain_hash = plan_chain_hash([p.plan_hash for p in passes])
    execution.fields = consolidate_fields(execution.fields)
    for error in _validation_errors(page):
        if error not in execution.validation_errors:
            execution.validation_errors.append(error)
    return execution


__all__ = [
    "AsyncUploadProtocol",
    "NativeFinalSubmitUploadContract",
    "LiveFormExecution",
    "SyntheticMutationExecution",
    "SyntheticMutationPass",
    "execute_live_form",
    "execute_live_form_synthetic",
    "execute_live_form_with_llm",
    "plan_chain_hash",
    "validate_typed_answer",
    "compute_field_token",
    "consolidate_fields",
]
