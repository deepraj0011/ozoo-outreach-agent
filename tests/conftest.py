from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from outreach.db import SessionLocal, init_db, make_engine
from outreach.models import Lead
from outreach.states import LeadState

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
_counter = itertools.count(1)


@pytest.fixture
def session():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    init_db(engine)
    s = SessionLocal(bind=engine)
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def make_lead(session: Session, **kw) -> Lead:
    n = next(_counter)
    defaults = dict(
        name="Test Lead",
        company="Acme",
        role="CTO",
        source="test",
        email=f"lead{n}@acme.com",
        consent_email=True,
        state=LeadState.IN_SEQUENCE.value,
        score=0,
        step_sent=0,
        created_at=NOW,
    )
    defaults.update(kw)
    if isinstance(defaults["state"], LeadState):
        defaults["state"] = defaults["state"].value
    lead = Lead(**defaults)
    session.add(lead)
    session.flush()
    return lead
