"""Task 12: channel starter / switcher (outreach.py)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from outreach.emailtool.fake import FakeEmailTool
from outreach.models import Alert as AlertRow
from outreach.models import Event
from outreach.outreach import Outreach, start_outreach, switch_channel
from outreach.states import LeadState
from outreach.whatsapp.fake import FakeWhatsApp

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_deps, make_settings

PHONE = "+919876543210"


def build(**settings_kw):
    settings = make_settings(**settings_kw)
    tool = FakeEmailTool(webhook_secret=WEBHOOK_SECRET)
    wa = FakeWhatsApp(settings)
    outreach = Outreach(settings)
    deps = make_deps(tool=tool, wa=wa, outreach=outreach)
    outreach.bind(deps)
    return deps, tool, wa, settings


def email_lead(session, **kw):
    kw.setdefault("verify_status", "valid")
    kw.setdefault("active_channel", "email")
    return make_lead(session, **kw)


def wa_lead(session, *, with_email=False, **kw):
    kw.setdefault("phone", PHONE)
    kw.setdefault("consent_whatsapp", True)
    kw.setdefault("active_channel", "whatsapp")
    if with_email:
        kw.setdefault("verify_status", "valid")
    else:
        kw["email"] = None
    return make_lead(session, **kw)


def names(calls):
    return [c[0] for c in calls]


def test_start_email_lead_enrolls(session):
    deps, tool, wa, _ = build()
    lead = email_lead(session)
    assert start_outreach(session, lead, deps, NOW) == "started"
    assert names(tool.calls) == ["add_lead"]
    assert lead.tool_lead_id == "fake-1"
    assert wa.calls == []


def test_start_email_lead_respects_eu_hold(session):
    deps, tool, _, _ = build()
    lead = email_lead(session, email_hold="eu_approval")
    assert start_outreach(session, lead, deps, NOW) == "held"
    assert tool.calls == []
    assert lead.tool_lead_id is None


def test_start_wa_lead_sends_intro_template(session):
    deps, tool, wa, _ = build()
    lead = wa_lead(session)
    assert start_outreach(session, lead, deps, NOW) == "sent"
    assert [(c[0], c[2]) for c in wa.calls] == [("send_template", "ozoo_intro")]
    assert lead.wa_template_sent_at == NOW
    assert lead.variant_flags["wa_intro_attempted_at"] == NOW.isoformat()
    assert tool.calls == []


def test_intro_never_sent_twice(session):
    deps, _, wa, _ = build()
    lead = wa_lead(session)
    start_outreach(session, lead, deps, NOW)
    assert start_outreach(session, lead, deps, NOW + timedelta(minutes=5)) == "already"
    assert len(wa.calls) == 1


def test_intro_not_resent_while_attempt_flag_set(session):
    """A send that died mid-flight (flag set, no sent_at) is never retried blindly."""
    deps, _, wa, _ = build()
    lead = wa_lead(session, variant_flags={"wa_intro_attempted_at": NOW.isoformat()})
    assert start_outreach(session, lead, deps, NOW) == "already"
    assert wa.calls == []


def test_intro_unknown_error_keeps_attempt_flag(session):
    deps, _, wa, _ = build()

    def boom(*_a, **_k):
        raise RuntimeError("socket died")

    wa.client.send_template = boom
    lead = wa_lead(session)
    assert start_outreach(session, lead, deps, NOW) == "error"
    assert lead.variant_flags["wa_intro_attempted_at"] == NOW.isoformat()
    assert lead.wa_template_sent_at is None
    kinds = session.scalars(select(AlertRow.kind)).all()
    assert kinds == ["integration_error"]


def test_intro_wa_send_error_falls_back_to_email_via_decide(session):
    deps, tool, wa, _ = build()
    wa.client.fail_code = 131026  # not on WhatsApp
    lead = wa_lead(session, with_email=True)
    assert start_outreach(session, lead, deps, NOW) == "failed"
    # decide(wa_failed 131026) -> SwitchChannel -> email enrolment
    assert lead.active_channel == "email"
    assert lead.channel_switched is True
    assert names(tool.calls) == ["add_lead"]
    ev = session.scalars(select(Event).where(Event.kind == "wa_failed")).one()
    assert ev.data["code"] == 131026 and ev.processed is True


def test_intro_wa_send_error_without_email_closes(session):
    deps, tool, wa, _ = build()
    wa.client.fail_code = 131026
    lead = wa_lead(session)
    start_outreach(session, lead, deps, NOW)
    assert lead.state == LeadState.CLOSED.value
    assert tool.calls == []


def test_both_channels_only_one_sends(session):
    deps, tool, wa, _ = build()
    lead = wa_lead(session, with_email=True)  # active_channel = whatsapp
    start_outreach(session, lead, deps, NOW)
    assert len(wa.calls) == 1 and tool.calls == []

    deps2, tool2, wa2, _ = build()
    lead2 = wa_lead(session, with_email=True, phone="+919876543211", active_channel="email")
    start_outreach(session, lead2, deps2, NOW)
    assert names(tool2.calls) == ["add_lead"] and wa2.calls == []


def test_switch_once_then_nurture(session):
    deps, tool, wa, _ = build()
    lead = wa_lead(session, with_email=True)
    start_outreach(session, lead, deps, NOW)
    assert switch_channel(session, lead, "wa_no_response", deps, NOW) == "email"
    assert lead.active_channel == "email" and lead.channel_switched is True
    assert lead.variant_flags["wa_reminder_cancelled"] is True
    assert names(tool.calls) == ["add_lead"]
    # second switch: no fallback left -> NURTURE, email stopped
    assert switch_channel(session, lead, "bounce", deps, NOW) is None
    assert lead.state == LeadState.NURTURE.value
    assert "stop" in names(tool.calls)
    assert len(wa.calls) == 1


def test_switch_without_fallback_goes_nurture(session):
    deps, tool, _, _ = build()
    lead = email_lead(session, tool_lead_id="t-1")
    assert switch_channel(session, lead, "bounce", deps, NOW) is None
    assert lead.state == LeadState.NURTURE.value
    assert lead.active_channel == "email"


def test_switch_of_paused_lead_does_not_send(session):
    deps, tool, wa, _ = build()
    lead = email_lead(session, phone=PHONE, consent_whatsapp=True, state=LeadState.PAUSED, tool_lead_id="t-1")
    assert switch_channel(session, lead, "bounce", deps, NOW) == "whatsapp"
    assert lead.active_channel == "whatsapp"
    assert wa.calls == []  # the worker's start_new sends once the lead is back in sequence


def test_port_switch_and_refresh(session):
    deps, tool, _, _ = build()
    lead = email_lead(session, phone=PHONE, consent_whatsapp=True, tool_lead_id="t-1", step_sent=1)
    deps.outreach.refresh_next_step(session, lead)
    assert names(tool.calls) == ["update_variables"]
    deps.outreach.switch(session, lead, "bounce", NOW)
    assert lead.active_channel == "whatsapp"


def test_send_email_text_replies_in_thread(session):
    deps, tool, _, _ = build()
    lead = email_lead(session, tool_lead_id="t-1")
    assert deps.outreach.send_email_text(session, lead, "booking_slots", NOW) is True
    assert tool.calls[0][0] == "reply" and tool.calls[0][1] == "t-1"


def test_send_email_text_unsupported_alerts(session):
    deps, tool, _, _ = build()
    tool.reply_supported = False
    lead = email_lead(session, tool_lead_id="t-1")
    assert deps.outreach.send_email_text(session, lead, "booking_slots", NOW) is False
    assert deps.alerter.calls == [("send", "unsure", lead.id)]


def test_default_deps_wires_outreach():
    from outreach.app import default_deps

    deps = default_deps(make_settings())
    assert isinstance(deps.outreach, Outreach)
    assert deps.outreach.deps is deps
