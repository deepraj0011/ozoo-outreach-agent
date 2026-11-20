from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from outreach.db import init_db, make_engine
from tests.fakes import TestSession
from outreach.models import Lead
from outreach.states import LeadState

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail closed: a real HTTP transport (httpx2, used by the LLM SDKs) never opens a socket in
    tests. MockTransport and respx-injected clients do not go through HTTPTransport."""
    import httpx2

    def _blocked(*_a, **_k):
        raise RuntimeError("network disabled in tests")

    async def _ablocked(*_a, **_k):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _blocked)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", _ablocked)


@pytest.fixture(autouse=True)
def _p0_campaign_started(monkeypatch):
    """Tests written before the P0 Start button assume sending has started; test_p0_campaign sets it back."""
    from outreach.p0 import campaign
    monkeypatch.setattr(campaign, "FRESH_RUNNING", True)


@pytest.fixture
def session():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    init_db(engine)
    s = TestSession(bind=engine)
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
