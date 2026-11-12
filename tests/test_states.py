from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from outreach.models import Event, Lead
from outreach.states import ALLOWED, TERMINAL, IllegalTransition, LeadState, transition
from tests.conftest import NOW, make_lead


def test_allowed_path(session):
    lead = make_lead(session)
    path = [LeadState.ENGAGED, LeadState.QUALIFYING, LeadState.BOOKED, LeadState.HANDED_OFF]
    for to in path:
        transition(session, lead, to, "test", NOW)
    assert lead.state == "handed_off"
    events = session.scalars(select(Event).where(Event.kind == "state_change")).all()
    assert len(events) == 4
    e = events[0]
    assert e.channel == "system" and e.verdict == "n/a" and e.processed is True
    assert e.data == {"from": "in_sequence", "to": "engaged", "reason": "test"}
    assert e.lead_id == lead.id


def test_illegal_transition_raises(session):
    lead = make_lead(session, state=LeadState.INTAKE)
    with pytest.raises(IllegalTransition):
        transition(session, lead, LeadState.BOOKED, "x", NOW)
    assert lead.state == "intake"
    assert session.scalars(select(Event)).all() == []


def test_any_non_terminal_can_be_suppressed(session):
    for st in LeadState:
        if st in TERMINAL:
            continue
        lead = make_lead(session, state=st)
        transition(session, lead, LeadState.SUPPRESSED, "optout", NOW)
        assert lead.state == "suppressed"


def test_terminal_states_are_final():
    for st in TERMINAL:
        if st is LeadState.HANDED_OFF:
            assert ALLOWED[st] == frozenset({LeadState.NURTURE})
        else:
            assert ALLOWED[st] == frozenset()


def test_lead_needs_email_or_phone(session):
    session.add(Lead(name="x", company="y", role="z", source="t", created_at=NOW,
                     state="intake", score=0, step_sent=0))
    with pytest.raises(IntegrityError):
        session.flush()
