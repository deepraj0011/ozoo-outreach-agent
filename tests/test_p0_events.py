"""P0 scheduled emails, task 6: unsubscribe / bounce / reply / booking stop the rest; clicks and visits
are recorded only; the lead goes to nurture after its last email."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from outreach import worker
from outreach.actions import execute
from outreach.classifier import ReplyClass, actions_for_class
from outreach.models import Alert as AlertRow
from outreach.models import Event, Lead
from outreach.p0 import flow
from outreach.states import LeadState

from .conftest import NOW
from .fakes import make_deps
from .test_p0_flow import S, SMTPStub, _reset_smtp, clock, env, event, rows, start, tool, tool_status  # noqa: F401

HOUR = timedelta(hours=1)


def _after_email_1(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    tool.send_due({})
    deps = make_deps(tool=tool)
    event(session, lead, "sent", NOW, deps, step=1)
    return lead, deps


def _classified(session, lead, deps, label):
    rc = ReplyClass(label=label, confidence=0.9, return_date=None, later_date=None, referral_email=None,
                    referral_phone=None, referral_name=None, reason="r", draft_answer=None)
    ev = event(session, lead, "reply", NOW + HOUR, deps, data={"text": "hello"})
    assert lead.state == LeadState.IN_SEQUENCE.value  # the label decides, not the reply itself
    execute(session, lead, actions_for_class(rc, lead, ev, NOW + HOUR), deps, NOW + HOUR, event=ev)
    session.flush()


def _alerts(session, kind=None):
    q = select(func.count(AlertRow.id))
    if kind:
        q = q.where(AlertRow.kind == kind)
    return session.scalar(q)


def test_unsubscribe_cancels_rest_and_blocks(session, tool):
    lead, deps = _after_email_1(session, tool)

    event(session, lead, "unsubscribe", NOW + HOUR, deps)

    assert [r.status for r in rows(session, lead)] == ["sent", "cancelled", "cancelled", "cancelled"]
    assert rows(session, lead)[1].reason == "unsubscribe"
    assert lead.state == LeadState.SUPPRESSED.value and tool_status(tool, lead) == "stopped"


def test_hard_bounce_cancels_rest(session, tool):
    lead, deps = _after_email_1(session, tool)
    lead.phone, lead.consent_whatsapp = "+919876543210", True

    event(session, lead, "bounce_hard", NOW + HOUR, deps)

    assert [r.status for r in rows(session, lead)][1:] == ["cancelled"] * 3
    assert deps.outreach.names() == ["switch"]


def test_reply_interested_stops_and_alerts_no_auto_reply(session, tool):
    lead, deps = _after_email_1(session, tool)

    _classified(session, lead, deps, "interested")

    assert [r.status for r in rows(session, lead)][1:] == ["cancelled"] * 3
    assert lead.state == LeadState.ENGAGED.value
    assert _alerts(session, "engaged") == 1
    assert "send_email_text" not in deps.outreach.names() and len(SMTPStub.sent) == 1


def test_ooo_reply_keeps_schedule(session, tool):
    lead, deps = _after_email_1(session, tool)

    _classified(session, lead, deps, "ooo")

    assert [r.status for r in rows(session, lead)] == ["sent", "planned", "planned", "planned"]
    assert lead.state == LeadState.IN_SEQUENCE.value


def test_reply_after_handoff_cancels_handed_row(session, tool):
    lead, deps = _after_email_1(session, tool)
    r2 = rows(session, lead)[1]
    assert flow.dispatch_due(session, lead, tool, r2.due_at + 10 * HOUR, settings=S)

    _classified(session, lead, deps, "question")

    assert rows(session, lead)[1].status == "cancelled"
    assert tool.send_due({}) == []


def test_click_and_visit_change_nothing(session, tool):
    lead, deps = _after_email_1(session, tool)
    before = [(r.status, r.due_at) for r in rows(session, lead)]

    event(session, lead, "click", NOW + HOUR, deps, tag="pricing", step=1)
    event(session, lead, "visit", NOW + HOUR, deps, tag="pricing", step=1)

    assert _alerts(session) == 0 and lead.score == 0
    assert [(r.status, r.due_at) for r in rows(session, lead)] == before
    assert lead.variant_flags["p0_clicks"] == 1 and lead.variant_flags["p0_visits"] == 1
    assert "send_email_text" not in deps.outreach.names()


def test_whatsapp_reply_cancels_email_rows(session, tool):
    lead, deps = _after_email_1(session, tool)
    lead.phone = "+919876543210"

    event(session, lead, "wa_text", NOW + HOUR, deps, channel="whatsapp", data={"text": "tell me more"})

    assert [r.status for r in rows(session, lead)][1:] == ["cancelled"] * 3


def test_booking_cancels_rest(session, tool):
    lead, deps = _after_email_1(session, tool)

    event(session, lead, "booking_created", NOW + HOUR, deps, channel="cal")

    assert [r.status for r in rows(session, lead)][1:] == ["cancelled"] * 3
    assert lead.state == LeadState.BOOKED.value


def test_finish_moves_to_nurture_without_alert(env, clock):
    with env.factory() as s:
        lead = start(s, env.tool)
        for r in rows(s, lead):
            r.status, r.sent_at = "sent", NOW - timedelta(days=5)
        s.commit()
        lead_id = lead.id

    worker.tick(env.factory, env.deps, clock, S)

    with env.factory() as s:
        lead = s.get(Lead, lead_id)
        assert lead.state == LeadState.NURTURE.value
        assert _alerts(s) == 0
        last = s.scalars(select(Event).where(Event.lead_id == lead_id, Event.kind == "state_change")
                         .order_by(Event.id.desc())).first()
        assert last.data["reason"] == "end_p0"
