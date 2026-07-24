"""Typed application settings loaded from environment variables.

All local-first configuration lives here. There is no global mutable state:
callers receive a frozen :class:`Settings` instance from :func:`load_settings`.

Per ``DEPLOYMENT_AND_REPO_STRATEGY.md`` defaults must be safe:

* bind to ``127.0.0.1`` (never public),
* ``submit_mode=review``,
* missing optional integration paths mark the integration unavailable in
  system health, but do not crash startup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SubmitMode = Literal["dry_run", "review", "trusted_auto_submit"]
ExecutionMode = Literal["sequential", "parallel"]


class Settings(BaseModel):
    """Resolved application settings.

    A frozen value object. Use :func:`load_settings` to build one from the
    environment (and an optional ``.env`` file).
    """

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Field(default=Path(".uaa_data"))
    jobhunter_queue: Path | None = Field(default=None)
    siemens_repo: Path | None = Field(default=None)
    browser_headless: bool = Field(default=False)
    browser_profile_dir: Path | None = Field(default=None)
    browser_channel: str | None = Field(default=None)
    browser_timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    browser_max_steps: int = Field(default=20, ge=1, le=100)
    submit_mode: SubmitMode = Field(default="review")
    # Controlled final submission gate. When False (the default), the
    # live browser runner NEVER clicks the final submit control, even if
    # the user has approved a snapshot. This is the hard kill switch.
    # When True, the SubmissionCoordinator may click submit ONLY after
    # every other gate (approval, snapshot match, no pending
    # interventions, no unresolved required fields, etc.) also passes.
    # See docs/generalization/DRY_RUN_LEVELS.md Level 3.
    enable_real_submission: bool = Field(default=False)
    # Execution mode: sequential (default) runs the UAA apply pipeline
    # one job at a time. Parallel allows ready-to-apply jobs to be
    # processed concurrently by a thread pool bounded by apply_workers.
    # NOTE: This only parallelizes the UAA apply phase (the
    # PipelineOrchestrator.run loop). It does NOT parallelize
    # JobHunter's scan/evaluate/tailor phases — those run in JobHunter,
    # not in UAA. The scan_workers/evaluate_workers/tailor_workers
    # config fields are reserved for future JobHunter-side concurrency
    # but are not yet wired to any parallel execution in this branch.
    execution_mode: ExecutionMode = Field(default="sequential")
    # Worker counts for each phase. Conservative defaults (1 = serial).
    # Currently, only apply_workers is used (by the UAA pipeline
    # orchestrator's parallel mode). scan_workers, evaluate_workers,
    # and tailor_workers are reserved for future JobHunter-side
    # parallelism and have no effect in this branch.
    scan_workers: int = Field(default=1, ge=1, le=16)
    evaluate_workers: int = Field(default=1, ge=1, le=16)
    tailor_workers: int = Field(default=1, ge=1, le=16)
    apply_workers: int = Field(default=1, ge=1, le=16)
    # Optional path to a candidate profile YAML (JobHunter's profile.yml).
    # Loaded by candidate_profile_loader.profile_from_config when the
    # per-job metadata does not contain a profile snapshot.
    candidate_profile: Path | None = Field(default=None)

    model_config = {"frozen": True, "extra": "ignore"}

    @field_validator("host")
    @classmethod
    def _deny_public_bind(cls, value: str) -> str:
        """Reject obvious public bind addresses at config load time.

        This is a guard rail, not a complete security control. The user can
        still explicitly opt into a public bind by setting ``UAA_HOST`` to a
        non-loopback address; we only refuse the wildcard ``0.0.0.0`` default
        which is the most common accidental-exposure case.
        """
        if value in {"0.0.0.0", "::"}:
            raise ValueError(
                "UAA_HOST=0.0.0.0 / :: would bind publicly. Version 1 must not "
                "expose the dashboard without authentication. Set UAA_HOST to "
                "127.0.0.1 explicitly to override only if you understand the risk."
            )
        return value


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"cannot parse boolean from {value!r}")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build a :class:`Settings` from the process environment.

    Environment variables are documented in ``.env.example``. Unknown
    variables are ignored. Empty strings for optional path settings are
    treated as unset.
    """
    source = env if env is not None else os.environ

    def _get_path(name: str) -> Path | None:
        raw = source.get(name, "").strip()
        return Path(raw) if raw else None

    host = source.get("UAA_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = source.get("UAA_PORT", "8000").strip() or "8000"
    port = int(port_raw)

    data_dir_raw = source.get("UAA_DATA_DIR", "").strip()
    data_dir = Path(data_dir_raw) if data_dir_raw else Path(".uaa_data")

    browser_headless_raw = source.get("UAA_BROWSER_HEADLESS", "false").strip()
    browser_headless = _parse_bool(browser_headless_raw) if browser_headless_raw else False

    submit_mode_raw = source.get("UAA_SUBMIT_MODE", "review").strip() or "review"
    execution_mode_raw = source.get("UAA_EXECUTION_MODE", "sequential").strip() or "sequential"

    enable_real_submission_raw = source.get("UAA_ENABLE_REAL_SUBMISSION", "false").strip()
    enable_real_submission = (
        _parse_bool(enable_real_submission_raw) if enable_real_submission_raw else False
    )

    def _parse_int(name: str, default: int, min_v: int = 1, max_v: int = 16) -> int:
        raw = source.get(name, "").strip()
        if not raw:
            return default
        try:
            val = int(raw)
        except ValueError:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from None
        if val < min_v or val > max_v:
            raise ValueError(f"{name} must be between {min_v} and {max_v}, got {val}")
        return val

    return Settings(
        host=host,
        port=port,
        data_dir=data_dir,
        jobhunter_queue=_get_path("UAA_JOBHUNTER_QUEUE"),
        siemens_repo=_get_path("UAA_SIEMENS_REPO"),
        browser_headless=browser_headless,
        browser_profile_dir=_get_path("UAA_BROWSER_PROFILE_DIR"),
        browser_channel=source.get("UAA_BROWSER_CHANNEL", "").strip() or None,
        browser_timeout_ms=_parse_int("UAA_BROWSER_TIMEOUT_MS", 30_000, 1_000, 120_000),
        browser_max_steps=_parse_int("UAA_BROWSER_MAX_STEPS", 20, 1, 100),
        submit_mode=submit_mode_raw,  # type: ignore[arg-type]
        execution_mode=execution_mode_raw,  # type: ignore[arg-type]
        enable_real_submission=enable_real_submission,
        scan_workers=_parse_int("UAA_SCAN_WORKERS", 1),
        evaluate_workers=_parse_int("UAA_EVALUATE_WORKERS", 1),
        tailor_workers=_parse_int("UAA_TAILOR_WORKERS", 1),
        apply_workers=_parse_int("UAA_APPLY_WORKERS", 1),
        candidate_profile=_get_path("UAA_CANDIDATE_PROFILE"),
    )
