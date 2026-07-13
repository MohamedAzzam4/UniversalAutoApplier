"""Queue importer for JobHunter ``application_queue.jsonl`` files.

Per ``ROADMAP.md`` WP 1.3 and ``DATA_CONTRACTS.md``:

- Read ``application_queue.jsonl`` (one JSON object per line).
- Validate each line as an :class:`ApplicationJob`.
- Insert or update jobs in history through store methods only.
- Do not directly mutate JSON from arbitrary modules.
- Importing the same queue twice is idempotent.
- Imported jobs retain document paths.
- No Siemens job ID is required for non-Siemens jobs.

Error handling:
- Malformed JSON (not parseable) produces a row-specific error; the import
  continues with the next line.
- Valid JSON that fails :class:`ApplicationJob` validation produces a
  row-specific error; the import continues.
- Relative artifact paths are rejected with a row-specific error (per
  DATA_CONTRACTS.md: "Relative paths are rejected with a row-specific
  contract error").
- The importer does **not** crash the entire import for one bad line.

Path handling:
- Per DATA_CONTRACTS.md: "Import normalizes path separators for the host OS,
  verifies existence for ready_to_apply, and stores the resolved path."
- We normalize separators using :class:`pathlib.Path` and resolve to
  absolute. Existence is checked for ``ready_to_apply`` jobs only (since
  test fixtures may not have real files for other statuses).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from universal_auto_applier.core.models import ApplicationJob
from universal_auto_applier.core.statuses import ApplicationStatus
from universal_auto_applier.persistence.db import session_scope
from universal_auto_applier.persistence.job_repository import upsert_application_job

logger = logging.getLogger("universal_auto_applier.queue_importer")


@dataclass
class ImportRowError:
    """A structured error for one bad JSONL line."""

    line_number: int
    raw_line: str
    error: str


@dataclass
class ImportResult:
    """The outcome of an import operation."""

    total_lines: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[ImportRowError] = field(default_factory=list[ImportRowError])

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def _read_jsonl(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, line_text)`` pairs from ``path``.

    Blank lines are skipped (line number is still incremented). Line numbers
    are 1-based.
    """
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            yield line_number, stripped


def _normalize_path(raw: str | None) -> str | None:
    """Normalize an artifact path for the host OS.

    Returns the resolved absolute path as a string, or None if input is None.
    Does NOT verify existence (that is done separately for ready_to_apply).
    """
    if raw is None:
        return None
    # Path() normalizes separators for the host OS. resolve() makes it
    # absolute and resolves symlinks.
    return str(Path(raw).resolve())


def _is_relative(raw: str) -> bool:
    """Return True if ``raw`` is a relative path."""
    return not Path(raw).is_absolute()


def _validate_and_build_job(line_number: int, raw_line: str) -> ApplicationJob | ImportRowError:
    """Parse and validate one JSONL line.

    Returns an :class:`ApplicationJob` on success, or an :class:`ImportRowError`
    on failure. Never raises.
    """
    # Parse JSON.
    try:
        parsed: Any = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        return ImportRowError(
            line_number=line_number,
            raw_line=raw_line,
            error=f"invalid JSON: {exc}",
        )

    if not isinstance(parsed, dict):
        return ImportRowError(
            line_number=line_number,
            raw_line=raw_line,
            error=f"expected JSON object, got {type(parsed).__name__}",
        )

    # Cast to dict[str, Any] — json.loads returns Any, but we've verified
    # it's a dict. Use typing.cast to satisfy Pyright without runtime cost.
    from typing import cast

    data: dict[str, Any] = cast(dict[str, Any], parsed)

    # Check for relative artifact paths before constructing the model.
    # Per DATA_CONTRACTS.md: relative paths are rejected with a row-specific
    # contract error.
    for field_name in ("cv_pdf", "cover_letter_pdf"):
        raw_path: Any = data.get(field_name)
        if raw_path is not None and isinstance(raw_path, str) and raw_path != "":
            if _is_relative(raw_path):
                return ImportRowError(
                    line_number=line_number,
                    raw_line=raw_line,
                    error=f"{field_name} must be an absolute path, got {raw_path!r}",
                )

    # Build the ApplicationJob. This validates all fields including the
    # deterministic application_id check.
    try:
        job = ApplicationJob(**data)
    except ValidationError as exc:
        # Collect all validation errors into a single message.
        messages: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            messages.append(f"{loc}: {err['msg']}")
        return ImportRowError(
            line_number=line_number,
            raw_line=raw_line,
            error="validation failed: " + "; ".join(messages),
        )

    # Normalize artifact paths for the host OS.
    if job.cv_pdf is not None:
        job.cv_pdf = _normalize_path(job.cv_pdf)
    if job.cover_letter_pdf is not None:
        job.cover_letter_pdf = _normalize_path(job.cover_letter_pdf)
    if job.documents is not None:
        if job.documents.cv_md is not None:
            job.documents.cv_md = _normalize_path(job.documents.cv_md)
        if job.documents.cover_letter_md is not None:
            job.documents.cover_letter_md = _normalize_path(job.documents.cover_letter_md)

    # Verify existence for ready_to_apply jobs.
    if job.status == ApplicationStatus.READY_TO_APPLY:
        for field_name, field_value in (
            ("cv_pdf", job.cv_pdf),
            ("cover_letter_pdf", job.cover_letter_pdf),
        ):
            if field_value is not None and not Path(field_value).exists():
                return ImportRowError(
                    line_number=line_number,
                    raw_line=raw_line,
                    error=f"{field_name} does not exist on disk: {field_value}",
                )

    return job


def import_queue_file(
    path: Path,
    session_factory: sessionmaker[Session],
) -> ImportResult:
    """Import a JobHunter ``application_queue.jsonl`` file.

    Args:
        path: Path to the JSONL file.
        session_factory: A SQLAlchemy session factory bound to the target
            database.

    Returns:
        An :class:`ImportResult` with counts and any row-level errors.

    The import is idempotent: re-importing the same file updates descriptive
    fields but does not duplicate jobs or erase attempt history.
    """
    result = ImportResult()

    for line_number, raw_line in _read_jsonl(path):
        result.total_lines += 1

        outcome = _validate_and_build_job(line_number, raw_line)

        if isinstance(outcome, ImportRowError):
            result.errors.append(outcome)
            result.skipped += 1
            logger.warning("[line %d] skipped: %s", line_number, outcome.error)
            continue

        job = outcome
        with session_scope(session_factory) as session:
            upsert_application_job(session, job)
        result.imported += 1
        logger.info(
            "[line %d] imported application_id=%s company=%s",
            line_number,
            job.application_id[:12],
            job.company,
        )

    logger.info(
        "import complete: total=%d imported=%d skipped=%d errors=%d",
        result.total_lines,
        result.imported,
        result.skipped,
        len(result.errors),
    )
    return result


__all__ = [
    "ImportRowError",
    "ImportResult",
    "import_queue_file",
]
