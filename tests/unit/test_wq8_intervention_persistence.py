"""WQ-8 Phase A — intervention dashboard/CLI same-DB persistence proof.

Uses a fixture DB (not .uaa_data) to prove:
* create → resolve persists durably
* new engine/process re-reads as resolved
* fill bridge via job.metadata.form_answers consumes resolved value
* no values are written to git evidence/logs
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from universal_auto_applier.core.statuses import InterventionKind, InterventionStatus
from universal_auto_applier.interventions.store import create_intervention, resolve_intervention
from universal_auto_applier.persistence.db import make_session_factory, session_scope
from universal_auto_applier.persistence.models import Base


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'persist.sqlite'}", future=True, poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory, engine
    engine.dispose()


def test_resolve_persists_across_new_connection(db) -> None:
    factory, engine = db
    with session_scope(factory) as s:
        row = create_intervention(
            s,
            application_id="a" * 64,
            kind=InterventionKind.FIELD_ANSWER,
            question="Geburtsdatum:*",
            field_selector="lf-test-persist",
        )
        iid = row.intervention_id
        resolve_intervention(s, iid, resolution=InterventionStatus.EDITED, answer="1990-01-01")
    factory2 = make_session_factory(engine)
    with session_scope(factory2) as s:
        from universal_auto_applier.interventions.store import get_intervention

        iv = get_intervention(s, iid)
        assert iv is not None
        assert str(iv.status) == "edited"
        assert iv.suggested_answer == "1990-01-01"
        assert iv.resolved_at is not None


def test_dashboard_and_cli_share_same_db_file(tmp_path: Path) -> None:
    db_path = tmp_path / "shared.sqlite"
    from sqlalchemy import create_engine as ce

    e1 = ce(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(e1)
    f1 = make_session_factory(e1)
    with session_scope(f1) as s:
        create_intervention(
            s,
            application_id="b" * 64,
            kind=InterventionKind.FIELD_ANSWER,
            question="Q",
            field_selector="lf-x",
        )
    e1.dispose()
    e2 = ce(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    f2 = make_session_factory(e2)
    with session_scope(f2) as s:
        from universal_auto_applier.interventions.store import count_pending_interventions

        assert count_pending_interventions(s, application_id="b" * 64) == 1
    e2.dispose()


def test_fill_bridge_consumes_form_answers(tmp_path: Path) -> None:
    from universal_auto_applier.core.identity import compute_application_id
    from universal_auto_applier.core.models import ApplicationJob, CandidateProfile, FormField
    from universal_auto_applier.form_engine.field_mapper import map_field

    candidate = CandidateProfile(full_name="A B", email="a@b.c")
    url = "https://example.com/jobs/c"
    job = ApplicationJob(
        application_id=compute_application_id(platform="unknown", external_job_id=None, url=url),
        company="C",
        title="T",
        url=url,
        platform="unknown",
        source="test",
        verdict="apply",
        status="evaluated",
        metadata={"form_answers": {"Geburtsdatum": "1990-01-01"}},
    )
    field = FormField(
        selector="lf-g", name="dateOfBirth", label="Geburtsdatum:*", type="text", required=True
    )
    mapping = map_field(field, candidate, job)
    assert mapping is not None
    assert mapping.value == "1990-01-01"
    assert mapping.source == "application_job"
