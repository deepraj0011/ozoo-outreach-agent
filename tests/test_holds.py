"""When paused colleagues restart (account rule), and releasing EU holds."""
from __future__ import annotations

from datetime import timedelta

import pytest

from outreach.holds import (
    COOL_OFF, QUIET_RESTART, colleague_resume_at, release_eu_holds, resume_account_holds,
)
from outreach.models import Event, Lead
from outreach.states import LeadState as S
from tests.conftest import NOW, make_lead

T0 = NOW - timedelta(days=200)


class Tool:
    def __init__(self, fail=False):
        self.resumed, self.fail = [], fail

    def resume(self, tool_lead_id):
        if self.fail:
            raise RuntimeError("down")
        self.resumed.append(tool_lead_id)


# ---------------------------------------------------------------- pure rule

@pytest.mark.parametrize("state,reason,exp", [
    (S.QUALIFYING, "interested", None),
    (S.BOOKED, "booking_created", None),
    (S.HANDED_OFF, "attended", None),
    (S.NURTURE, "sales_rejected", T0),
    (S.CLOSED, "wrong_person", T0),
    (S.CLOSED, "not_interested", T0 + COOL_OFF),
    (S.SUPPRESSED, "unsubscribe", T0 + COOL_OFF),
    (S.NURTURE, "low_fit", T0 + COOL_OFF),
    (S.ARCHIVED, "silent", T0),
    (S.IN_SEQUENCE, "x", T0),
])
def test_resume_rule(state, reason, exp):
    assert colleague_resume_at(state, reason, T0, None, None) == exp


def test_not_now_waits_for_later_date():
    later = T0 + timedelta(days=40)
    assert colleague_resume_at(S.NURTURE, "not_now", T0, None, later) == later
    assert colleague_resume_at(S.NURTURE, "not_now", T0, None, None) == T0 + COOL_OFF


def test_engaged_restarts_after_14_quiet_days():
    last = T0 + timedelta(days=3)
    assert colleague_resume_at(S.ENGAGED, "reply", T0, last, None) == last + QUIET_RESTART
    assert colleague_resume_at(S.ENGAGED, "reply", T0, None, None) == T0 + QUIET_RESTART


# ---------------------------------------------------------------- DB job

def _change(session, lead, to, reason, at):
    lead.state = to.value
    session.add(Event(lead_id=lead.id, channel="system", kind="state_change", occurred_at=at,
                      received_at=at, verdict="n/a", processed=True,
                      data={"to": to.value, "reason": reason}))
    session.flush()


def _pair(session, blocker_state, reason, at, **bk):
    ceo = make_lead(session, email="ceo@corp.com", domain="corp.com", **bk)
    _change(session, ceo, blocker_state, reason, at)
    cto = make_lead(session, email="cto@corp.com", domain="corp.com", state=S.PAUSED,
                    pause_reason="account", tool_lead_id="t-cto",
                    variant_flags={"paused_by": ceo.id, "wa_reminder_cancelled": True})
    return ceo, cto


def test_ceo_not_now_cto_waits_until_later_date(session):
    later = NOW + timedelta(days=30)
    _, cto = _pair(session, S.NURTURE, "not_now", NOW - timedelta(days=1), nurture_until=later)
    tool = Tool()
    assert resume_account_holds(session, tool, NOW) == 0
    assert resume_account_holds(session, tool, later) == 1
    assert cto.state == "in_sequence" and cto.pause_reason is None
    assert "paused_by" not in cto.variant_flags and "wa_reminder_cancelled" not in cto.variant_flags
    assert tool.resumed == ["t-cto"]


def test_ceo_booked_cto_stays_paused(session):
    _, cto = _pair(session, S.BOOKED, "booking_created", T0)
    assert resume_account_holds(session, Tool(), NOW) == 0 and cto.state == "paused"


def test_ceo_wrong_person_cto_restarts_now(session):
    _, cto = _pair(session, S.CLOSED, "wrong_person", NOW)
    assert resume_account_holds(session, Tool(), NOW) == 1 and cto.state == "in_sequence"


def test_ceo_not_interested_cto_restarts_after_90_days(session):
    _, cto = _pair(session, S.CLOSED, "not_interested", NOW)
    assert resume_account_holds(session, Tool(), NOW + COOL_OFF - timedelta(minutes=1)) == 0
    assert resume_account_holds(session, Tool(), NOW + COOL_OFF) == 1


def test_ceo_goes_quiet_cto_restarts_after_14_days(session):
    ceo, cto = _pair(session, S.ENGAGED, "reply", NOW)
    reply_at = NOW + timedelta(days=2)
    session.add(Event(lead_id=ceo.id, channel="email", kind="reply", occurred_at=reply_at,
                      received_at=reply_at, verdict="human", processed=True, data={}))
    session.flush()
    assert resume_account_holds(session, Tool(), NOW + QUIET_RESTART) == 0
    assert resume_account_holds(session, Tool(), reply_at + QUIET_RESTART) == 1


def test_any_other_active_colleague_keeps_hold(session):
    _, cto = _pair(session, S.CLOSED, "wrong_person", NOW)
    make_lead(session, email="cfo@corp.com", domain="corp.com", state=S.BOOKED)
    assert resume_account_holds(session, Tool(), NOW) == 0 and cto.state == "paused"


def test_ooo_pauses_untouched(session):
    lead = make_lead(session, state=S.PAUSED, pause_reason="ooo")
    assert resume_account_holds(session, Tool(), NOW) == 0 and lead.state == "paused"


def test_never_started_colleague_restarts_without_tool_call(session):
    ceo, cto = _pair(session, S.CLOSED, "wrong_person", NOW)
    cto.tool_lead_id = None
    tool = Tool()
    assert resume_account_holds(session, tool, NOW) == 1 and tool.resumed == []


def test_tool_failure_is_recorded_not_raised(session):
    from outreach.models import Alert
    _pair(session, S.CLOSED, "wrong_person", NOW)
    assert resume_account_holds(session, Tool(fail=True), NOW) == 1
    assert session.query(Alert).filter_by(kind="integration_error").count() == 1


# ---------------------------------------------------------------- EU release

def test_release_eu_holds(session):
    waiting = make_lead(session, state=S.PAUSED, pause_reason="eu_approval",
                        email_hold="eu_approval", is_eu=True)
    on_wa = make_lead(session, email_hold="eu_approval", is_eu=True, active_channel="whatsapp")
    assert release_eu_holds(session, Tool(), NOW) == 1
    assert waiting.state == "in_sequence" and waiting.email_hold is None
    assert on_wa.state == "in_sequence" and on_wa.email_hold is None  # email now usable as fallback


def test_release_eu_respects_account_rule(session):
    boss = make_lead(session, email="boss@firma.de", domain="firma.de", state=S.ENGAGED)
    lead = make_lead(session, email="anna@firma.de", domain="firma.de", state=S.PAUSED,
                     pause_reason="eu_approval", email_hold="eu_approval", is_eu=True)
    assert release_eu_holds(session, Tool(), NOW) == 0
    assert lead.state == "paused" and lead.pause_reason == "account"
    assert lead.variant_flags["paused_by"] == boss.id
