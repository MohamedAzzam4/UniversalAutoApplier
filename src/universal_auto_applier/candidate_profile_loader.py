"""Candidate profile loader.

Resolves the :class:`CandidateProfile` used by the pipeline for form
filling. The profile is loaded from (in priority order):

1. **Per-job metadata**: if the imported :class:`ApplicationJob` has a
   ``metadata.candidate_profile`` snapshot (written by JobHunter's
   queue exporter), use it. This is the per-job, per-export source of
   truth and lets different jobs carry different profile snapshots
   (e.g. if the candidate updated their profile between exports).

2. **Shared config file**: if the env var ``UAA_CANDIDATE_PROFILE``
   points at a YAML file (e.g. JobHunter's ``config/profile.yml``),
   load the profile from there. This is the fallback when the queue
   rows don't carry a snapshot (e.g. legacy queue files).

3. **Empty default**: if neither source is available, return an empty
   :class:`CandidateProfile()`. This preserves the previous behavior
   so existing tests that don't provide a profile still pass. However,
   the pipeline orchestrator and API now log a warning when falling
   back to the empty default, so the missing-profile bug is visible.

Per the integration design (see
``docs/generalization/PHASE_7_ATS_ADAPTERS.md`` and the
``checkpoint/jobhunter-uaa-integration`` branch), the candidate profile
is transported inside ``ApplicationJob.metadata["candidate_profile"]``
rather than as a top-level contract field. This avoids a breaking
schema change to :class:`ApplicationJob` while still letting UAA fill
forms with real candidate data.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from universal_auto_applier.core.models import CandidateProfile

logger = logging.getLogger("universal_auto_applier.candidate_profile")


def profile_from_metadata(metadata: dict[str, Any] | None) -> CandidateProfile | None:
    """Extract a :class:`CandidateProfile` from job metadata.

    Returns None if the metadata does not contain a ``candidate_profile``
    snapshot. The caller should then fall back to
    :func:`profile_from_config` or :class:`CandidateProfile()`.

    Args:
        metadata: The ``ApplicationJob.metadata`` dict, or None.

    Returns:
        A :class:`CandidateProfile` populated from the snapshot, or None.
    """
    if not metadata:
        return None
    snap_raw = metadata.get("candidate_profile")
    if not isinstance(snap_raw, dict):
        return None
    # Narrow to dict[str, Any] for type safety.
    from typing import cast

    snap = cast(dict[str, Any], snap_raw)
    # Filter to known CandidateProfile fields (ignore extra keys like
    # exported_at, score_breakdown).
    known_fields: set[str] = {
        "first_name",
        "last_name",
        "full_name",
        "email",
        "phone",
        "linkedin_url",
        "city",
        "country",
        "requires_sponsorship",
        "work_authorization",
        "years_of_experience",
        "current_position",
        "website",
        "github_url",
        "salutation",
        "academic_title",
    }
    filtered: dict[str, Any] = {
        k: v for k, v in snap.items() if k in known_fields and v is not None
    }
    if not filtered:
        return None
    try:
        profile = CandidateProfile(**filtered)
        logger.info(
            "candidate profile loaded from job metadata: email=%s, name=%s",
            profile.email or "(none)",
            profile.full_name or "(none)",
        )
        return profile
    except Exception as exc:
        logger.warning("failed to build CandidateProfile from metadata: %s", exc)
        return None


def profile_from_config(config_path: Path | None = None) -> CandidateProfile | None:
    """Load a :class:`CandidateProfile` from a shared YAML config file.

    The config file is expected to be in JobHunter's ``profile.yml``
    format (with a top-level ``candidate`` key). This is the fallback
    when queue rows don't carry a per-job snapshot.

    Args:
        config_path: Path to the YAML file. If None, reads from the
            ``UAA_CANDIDATE_PROFILE`` env var. If the env var is not
            set, returns None.

    Returns:
        A :class:`CandidateProfile`, or None if no config file is
        available.
    """
    import os

    if config_path is None:
        env_path = os.environ.get("UAA_CANDIDATE_PROFILE", "").strip()
        if not env_path:
            return None
        config_path = Path(env_path)
    if not config_path.exists():
        logger.warning("UAA_CANDIDATE_PROFILE path does not exist: %s", config_path)
        return None

    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as f:
            data_raw: Any = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("failed to load candidate profile from %s: %s", config_path, exc)
        return None

    if not isinstance(data_raw, dict):
        return None
    # yaml.safe_load returns Any; use typing.cast to narrow to dict[str, Any]
    # after the isinstance check above.
    from typing import cast

    data = cast(dict[str, Any], data_raw)
    cand_raw: Any = data.get("candidate")
    if not isinstance(cand_raw, dict):
        return None
    cand = cast(dict[str, Any], cand_raw)
    if not cand:
        return None

    # Map JobHunter's profile.yml fields to CandidateProfile.
    full_name_raw: Any = cand.get("full_name", "")
    full_name: str = str(full_name_raw) if full_name_raw else ""
    parts = full_name.split(maxsplit=1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""

    location_raw: Any = cand.get("location", "")
    location: str = str(location_raw) if location_raw else ""
    if "," in location:
        city_str, country_str = (s.strip() for s in location.split(",", 1))
        city: str = city_str
        country: str = country_str
    else:
        city = location
        country = ""

    email_raw: Any = cand.get("email", "")
    phone_raw: Any = cand.get("phone", "")
    linkedin_raw: Any = cand.get("linkedin", "")
    github_raw: Any = cand.get("github", "")
    subtitle_raw: Any = cand.get("subtitle", "")

    try:
        profile = CandidateProfile(
            first_name=first_name,
            last_name=last_name,
            full_name=full_name,
            email=str(email_raw) if email_raw else "",
            phone=str(phone_raw) if phone_raw else "",
            linkedin_url=str(linkedin_raw) if linkedin_raw else "",
            github_url=str(github_raw) if github_raw else "",
            city=city,
            country=country,
            current_position=str(subtitle_raw) if subtitle_raw else "",
            salutation=str(cand.get("salutation", "")) or None,
            academic_title=str(cand.get("academic_title", "")) or None,
        )
        logger.info(
            "candidate profile loaded from %s: email=%s, name=%s",
            config_path,
            profile.email or "(none)",
            profile.full_name or "(none)",
        )
        return profile
    except Exception as exc:
        logger.warning("failed to build CandidateProfile from %s: %s", config_path, exc)
        return None


def resolve_candidate_profile(
    job_metadata: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> CandidateProfile:
    """Resolve the candidate profile using the full fallback chain.

    Priority:
    1. Per-job metadata (``job_metadata["candidate_profile"]``).
    2. Shared config file (``UAA_CANDIDATE_PROFILE`` env var or
       ``config_path``).
    3. Empty :class:`CandidateProfile()` (with a warning).

    Args:
        job_metadata: The ApplicationJob.metadata dict, or None.
        config_path: Optional explicit path to a YAML config file.

    Returns:
        A :class:`CandidateProfile`. Never None.
    """
    # 1. Per-job metadata.
    profile = profile_from_metadata(job_metadata)
    if profile is not None:
        return profile

    # 2. Shared config file.
    profile = profile_from_config(config_path)
    if profile is not None:
        return profile

    # 3. Empty default (with warning).
    logger.warning(
        "no candidate profile found in job metadata or UAA_CANDIDATE_PROFILE; "
        "falling back to empty CandidateProfile(). Form fields requiring "
        "candidate data (name, email, phone) will become interventions."
    )
    return CandidateProfile()


__all__ = [
    "profile_from_metadata",
    "profile_from_config",
    "resolve_candidate_profile",
]
