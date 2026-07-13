"""interventions package.

Phase 5 implements:
- :mod:`store` — InterventionStore (create, resolve, list)
- :mod:`answer_memory` — AnswerMemoryStore (store, retrieve, edit, delete)
- :mod:`review` — ReviewBeforeSubmit (review state, approval gate)
- :mod:`fill_bridge` — Bridge from Phase 4 fill results to interventions

See ``docs/generalization/ROADMAP.md`` Phase 5 for details.
"""

from __future__ import annotations

from universal_auto_applier.interventions.answer_memory import (
    delete_answer,
    list_answers,
    normalize_question,
    retrieve_answer,
    store_answer,
    update_answer,
)
from universal_auto_applier.interventions.fill_bridge import (
    create_interventions_from_fill_summary,
)
from universal_auto_applier.interventions.navigation_bridge import (
    create_interventions_from_exploration,
)
from universal_auto_applier.interventions.review import (
    ReviewState,
    approve_review_state,
    check_submit_approval,
    create_review_state,
)
from universal_auto_applier.interventions.store import (
    count_pending_interventions,
    create_intervention,
    get_intervention,
    list_all_interventions,
    list_pending_interventions,
    resolve_intervention,
)

__all__ = [
    # Store
    "create_intervention",
    "resolve_intervention",
    "list_pending_interventions",
    "list_all_interventions",
    "get_intervention",
    "count_pending_interventions",
    # Answer memory
    "normalize_question",
    "store_answer",
    "retrieve_answer",
    "list_answers",
    "delete_answer",
    "update_answer",
    # Review
    "ReviewState",
    "create_review_state",
    "approve_review_state",
    "check_submit_approval",
    # Fill bridge
    "create_interventions_from_fill_summary",
    # Navigation bridge
    "create_interventions_from_exploration",
]
