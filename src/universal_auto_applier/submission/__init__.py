"""Controlled final submission package."""

from universal_auto_applier.submission.execution_service import (
    BrowserContextFactory,
    FixtureContextFactory,
    PlaywrightContextFactory,
    SubmissionExecutionService,
)
from universal_auto_applier.submission.models import (
    SubmissionApproval,
    SubmissionClaim,
    SubmissionResult,
    SubmissionResultState,
    SubmissionSnapshot,
    SubmissionSnapshotDocument,
    SubmissionSnapshotField,
    SubmissionSnapshotSubmitControl,
    build_snapshot_from_report,
)

__all__ = [
    "BrowserContextFactory",
    "FixtureContextFactory",
    "PlaywrightContextFactory",
    "SubmissionApproval",
    "SubmissionClaim",
    "SubmissionExecutionService",
    "SubmissionResult",
    "SubmissionResultState",
    "SubmissionSnapshot",
    "SubmissionSnapshotDocument",
    "SubmissionSnapshotField",
    "SubmissionSnapshotSubmitControl",
    "build_snapshot_from_report",
]
