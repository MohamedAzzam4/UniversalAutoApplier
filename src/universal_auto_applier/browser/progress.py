"""Privacy-safe progress checks for bounded application-form navigation."""

from __future__ import annotations

import hashlib
import json

from playwright.sync_api import Page

from universal_auto_applier.browser.live_models import LiveFieldRecord, LiveUploadRecord
from universal_auto_applier.core.statuses import ClickableClassification
from universal_auto_applier.navigator.apply_path_finder import LiveClickable, LivePageAnalysis

_PROGRESS_SCHEMA_JS = r"""
async () => {
  const text = (node) => (node && (node.innerText || node.textContent) || '').trim();
  const visible = (el) => {
    const style = getComputedStyle(el);
    return el.getClientRects().length > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const controls = Array.from(document.querySelectorAll(
    "input:not([type='hidden']):not([type='button']):not([type='submit']):not([type='reset']):not([type='image']), textarea, select"
  )).filter(visible).map((el) => {
    const labels = Array.from(el.labels || []).map(text).filter(Boolean);
    const selectedOptions = el.tagName === 'SELECT'
      ? Array.from(el.options || []).map((option, index) => ({option, index}))
          .filter(({option}) => option.selected)
          .map(({option, index}) => ({index, label: text(option).slice(0, 200)}))
      : [];
    const selected = selectedOptions.length;
    return {
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      label: labels.join(' ').slice(0, 300),
      ariaLabel: el.getAttribute('aria-label') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      disabled: !!el.disabled,
      checked: !!el.checked,
      hasValue: el.type === 'file' ? !!(el.files && el.files.length) :
        (el.tagName === 'SELECT' ? selected > 0 : String(el.value || '').length > 0),
      selectedCount: selected,
      selectedOptions,
      optionCount: el.tagName === 'SELECT' ? (el.options || []).length : 0
    };
  });
  const stepMarkers = Array.from(document.querySelectorAll(
    "h1, h2, h3, legend, [aria-current='step'], [role='progressbar'], [data-step]"
  )).filter(visible).slice(0, 32).map((el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    ariaCurrent: el.getAttribute('aria-current') || '',
    ariaValueNow: el.getAttribute('aria-valuenow') || '',
    ariaValueMax: el.getAttribute('aria-valuemax') || '',
    dataStep: el.getAttribute('data-step') || '',
    text: text(el).slice(0, 200)
  }));
  const payload = JSON.stringify({controls, stepMarkers});
  if (globalThis.crypto && globalThis.crypto.subtle) {
    const bytes = new TextEncoder().encode(payload);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let first = 2166136261;
  let second = 2246822519;
  for (let i = 0; i < payload.length; i += 1) {
    const code = payload.charCodeAt(i);
    first = Math.imul(first ^ code, 16777619);
    second = Math.imul(second ^ code, 3266489917);
  }
  return `${first >>> 0}-${second >>> 0}`;
}
"""

_STEP_SCHEMA_JS = r"""
async () => {
  const text = (node) => (node && (node.innerText || node.textContent) || '').trim();
  const visible = (el) => {
    const style = getComputedStyle(el);
    return el.getClientRects().length > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  const controls = Array.from(document.querySelectorAll(
    "input:not([type='hidden']):not([type='button']):not([type='submit']):not([type='reset']):not([type='image']), textarea, select"
  )).filter(visible).map((el) => ({
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    id: el.id || '',
    name: el.getAttribute('name') || '',
    label: Array.from(el.labels || []).map(text).filter(Boolean).join(' ').slice(0, 300),
    ariaLabel: el.getAttribute('aria-label') || '',
    required: !!el.required || el.getAttribute('aria-required') === 'true',
    disabled: !!el.disabled,
    optionLabels: el.tagName === 'SELECT'
      ? Array.from(el.options || []).map((option) => text(option).slice(0, 200))
      : []
  }));
  const stepMarkers = Array.from(document.querySelectorAll(
    "h1, h2, h3, legend, [aria-current='step'], [role='progressbar'], [data-step]"
  )).filter(visible).slice(0, 32).map((el) => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    ariaCurrent: el.getAttribute('aria-current') || '',
    ariaValueNow: el.getAttribute('aria-valuenow') || '',
    ariaValueMax: el.getAttribute('aria-valuemax') || '',
    dataStep: el.getAttribute('data-step') || '',
    text: text(el).slice(0, 200)
  }));
  const payload = JSON.stringify({controls, stepMarkers});
  if (globalThis.crypto && globalThis.crypto.subtle) {
    const bytes = new TextEncoder().encode(payload);
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let first = 2166136261;
  let second = 2246822519;
  for (let i = 0; i < payload.length; i += 1) {
    const code = payload.charCodeAt(i);
    first = Math.imul(first ^ code, 16777619);
    second = Math.imul(second ^ code, 3266489917);
  }
  return `${first >>> 0}-${second >>> 0}`;
}
"""

_VISIBLE_ANSWER_CONTROL_JS = r"""
() => {
  const visible = (el) => {
    const style = getComputedStyle(el);
    return el.getClientRects().length > 0 && style.visibility !== 'hidden' &&
      style.display !== 'none';
  };
  return Array.from(document.querySelectorAll(
    "input:not([type='hidden']):not([type='file']):not([type='button']):not([type='submit']):not([type='reset']):not([type='image']), textarea, select"
  )).some(visible);
}
"""

_BLOCKING_FIELD_STATUSES = frozenset({"blocked", "intervention_needed", "failed"})
_READY_UPLOAD_STATUSES = frozenset({"selection_verified", "remote_accepted"})


def form_progress_fingerprint(page: Page) -> str:
    """Return a digest of URL + visible form schema/state, never raw values.

    The page evaluates and hashes its own control metadata. Only per-frame
    digests cross the Playwright boundary; the digest is neither logged nor
    persisted by this helper. Text values, email addresses, document names,
    and option values are excluded.
    """
    frame_digests: list[str] = []
    for frame in page.frames:
        try:
            digest = frame.evaluate(_PROGRESS_SCHEMA_JS)
        except Exception:
            digest = "unavailable"
        frame_digests.append(str(digest))

    payload = json.dumps(
        {"url": page.url, "frames": frame_digests},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def form_step_schema_fingerprint(page: Page) -> str:
    """Return a stable per-step schema digest without exposing field values."""
    frame_digests: list[str] = []
    for frame in page.frames:
        try:
            digest = frame.evaluate(_STEP_SCHEMA_JS)
        except Exception:
            digest = "unavailable"
        frame_digests.append(str(digest))
    return hashlib.sha256(",".join(frame_digests).encode("utf-8")).hexdigest()


def has_visible_answer_controls(page: Page) -> bool:
    """Detect visible answer fields while excluding file-only intro pages."""
    for frame in page.frames:
        try:
            if frame.evaluate(_VISIBLE_ANSWER_CONTROL_JS):
                return True
        except Exception:
            continue
    return False


def is_form_step_candidate(page: Page, analysis: LivePageAnalysis) -> bool:
    """Recognize a one-field wizard step missed by the broad page heuristic.

    The fallback requires a visible answer control plus either a safe Continue
    or dangerous final-submit candidate. File-only intros remain navigation
    pages. Ambiguous action sets are still routed through form gating and will
    be stopped before a click.
    """
    if analysis.is_application_form:
        return True
    if not has_visible_answer_controls(page):
        return False
    return any(
        item.classification
        in {
            ClickableClassification.SAFE_CONTINUE,
            ClickableClassification.DANGEROUS_SUBMIT,
        }
        for item in analysis.clickables
    )


def uniquely_safe_form_continue(analysis: LivePageAnalysis) -> bool:
    """Return true only when one Continue is visible and no submit is mixed in."""
    continues = [
        item
        for item in analysis.clickables
        if item.classification == ClickableClassification.SAFE_CONTINUE
    ]
    submits = [
        item
        for item in analysis.clickables
        if item.classification == ClickableClassification.DANGEROUS_SUBMIT
    ]
    return len(continues) == 1 and not submits


def step_scoped_fields(fields: list[LiveFieldRecord], page: Page) -> list[LiveFieldRecord]:
    """Attach a stable step identity while preserving executor field tokens.

    The digest uses only visible control schema and step markers, not answer
    values or selected state. Reports and frozen mutation plans keep their
    original field_token; consumers that accumulate across steps can use the
    composite (step_identity, field_token) identity.
    """
    schema_fingerprint = form_step_schema_fingerprint(page)
    return [field.model_copy(update={"step_identity": schema_fingerprint}) for field in fields]


def action_progress_fingerprint(action: LiveClickable) -> str:
    """Hash the safe action identity without retaining its display text."""
    payload = json.dumps(
        {
            "classification": action.classification.value,
            "selector": action.selector_hint,
            "frame_url": action.frame_url,
            "text": action.text,
            "aria_label": action.aria_label,
            "href": action.href,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProgressTracker:
    """Reject an unchanged state/action pair while allowing same-URL steps."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    def register(self, page: Page, action: LiveClickable) -> bool:
        key = (form_progress_fingerprint(page), action_progress_fingerprint(action))
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def has_final_review_boundary(
    analysis: LivePageAnalysis, *, answer_controls_present: bool | None = None
) -> bool:
    """Require one identifiable final submit control and no safe Continue."""
    has_form_evidence = (
        analysis.is_application_form
        if answer_controls_present is None
        else analysis.is_application_form or answer_controls_present
    )
    if not has_form_evidence or analysis.blocker or analysis.expired or analysis.submitted:
        return False
    submit_controls = [
        item
        for item in analysis.clickables
        if item.classification == ClickableClassification.DANGEROUS_SUBMIT
    ]
    continue_controls = [
        item
        for item in analysis.clickables
        if item.classification == ClickableClassification.SAFE_CONTINUE
    ]
    if len(submit_controls) != 1 or continue_controls:
        return False
    submit = submit_controls[0]
    return bool(submit.selector_hint.strip() and (submit.text.strip() or submit.aria_label.strip()))


def form_step_has_unresolved_work(
    fields: list[LiveFieldRecord],
    uploads: list[LiveUploadRecord],
    *,
    required_unresolved: int = 0,
    validation_errors: list[str] | None = None,
) -> bool:
    """Fail closed before Continue when a field or upload lacks read-back."""
    if required_unresolved > 0 or validation_errors:
        return True
    for record in fields:
        if record.status in _BLOCKING_FIELD_STATUSES:
            return True
        if record.required and record.status != "filled" and not record.selected_value.strip():
            return True
    return any(upload.status not in _READY_UPLOAD_STATUSES for upload in uploads)


def final_boundary_metadata(
    analysis: LivePageAnalysis, *, answer_controls_present: bool | None = None
) -> tuple[bool, str, str, str]:
    """Return the final-boundary flag and unique control identity, if any."""
    if not has_final_review_boundary(analysis, answer_controls_present=answer_controls_present):
        return False, "", "", ""
    submit = next(
        item
        for item in analysis.clickables
        if item.classification == ClickableClassification.DANGEROUS_SUBMIT
    )
    return True, submit.text or submit.aria_label, submit.selector_hint, submit.frame_url


def consolidated_uploads(uploads: list[LiveUploadRecord]) -> list[LiveUploadRecord]:
    """Keep the latest evidence for repeated upload controls across steps."""
    by_identity: dict[tuple[str, str, str], LiveUploadRecord] = {}
    order: list[tuple[str, str, str]] = []
    for upload in uploads:
        key = (upload.document_kind, upload.path, upload.selector)
        if key not in by_identity:
            order.append(key)
        by_identity[key] = upload
    return [by_identity[key] for key in order]


__all__ = [
    "ProgressTracker",
    "action_progress_fingerprint",
    "consolidated_uploads",
    "final_boundary_metadata",
    "form_progress_fingerprint",
    "form_step_schema_fingerprint",
    "form_step_has_unresolved_work",
    "has_visible_answer_controls",
    "has_final_review_boundary",
    "is_form_step_candidate",
    "step_scoped_fields",
    "uniquely_safe_form_continue",
]
