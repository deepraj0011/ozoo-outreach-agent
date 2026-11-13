from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from outreach.actions import (
    AddScore, Alert, BotReply, Classify, Deps, HandOff, PauseColleagues, RefreshNextStep, SendReply,
    SetFlag, SetState, SetStepSent, SetVariant, StopAllOutreach, Suppress, SuppressAllChannels,
    SwitchChannel, ToolPause, ToolResume, ToolStop, WaCancelReminder, WaSendTemplate, WaSendText,
    execute,
)
from outreach.models import Alert as AlertRow
from outreach.models import Event, Suppression
from outreach.states import LeadState as S
from tests.conftest import NOW, make_lead
from tests.fakes import make_deps


def alerts(session, kind=None):
    rows = session.scalars(select(AlertRow)).all()
    return [r for r in rows if kind is None or r.kind == kind]


def test_execute_stop_calls_tool(session):
    lead = make_lead(session, tool_lead_id="T-1")
    deps = make_deps()
    execute(session, lead, [ToolStop()], deps, NOW)
    assert deps.tool.calls == [("stop", "T-1")]


def test_tool_actions_skipped_without_tool_lead_id(session):
    lead = make_lead(session)
    deps = make_deps()
    execute(session, lead, [ToolStop(), ToolPause(NOW), ToolResume()], deps, NOW)
    assert deps.tool.calls == []
    assert lead.pause_until == NOW


def test_tool_pause_resume_and_pause_until(session):
    lead = make_lead(session, tool_lead_id="T-1")
    deps = make_deps()
    until = NOW + timedelta(days=3)
    execute(session, lead, [ToolPause(until), ToolResume()], deps, NOW)
    assert deps.tool.calls == [("pause", "T-1"), ("resume", "T-1")]
    assert lead.pause_until == until


def test_stop_all_outreach_expands(session):
    lead = make_lead(session, tool_lead_id="T-1")
    deps = make_deps()
    execute(session, lead, [StopAllOutreach()], deps, NOW)
    assert deps.tool.calls == [("stop", "T-1")]
    assert lead.variant_flags["wa_reminder_cancelled"] is True


def test_chained_set_states_in_order(session):
    lead = make_lead(session, state=S.PAUSED)
    execute(session, lead, [SetState(S.ENGAGED, "x"), SetState(S.QUALIFYING, "x")], make_deps(), NOW)
    assert lead.state == "qualifying"
    changes = session.scalars(select(Event).where(Event.kind == "state_change").order_by(Event.id)).all()
    assert [c.data["to"] for c in changes] == ["engaged", "qualifying"]


def test_execute_illegal_transition_alerts_not_raises(session):
    lead = make_lead(session, state=S.IN_SEQUENCE)
    deps = make_deps()
    execute(session, lead, [SetState(S.HANDED_OFF, "bad"), SetFlag("after", 1)], deps, NOW)
    assert lead.state == "in_sequence"
    assert len(alerts(session, "integration_error")) == 1
    assert lead.variant_flags["after"] == 1  # continued


def test_suppress_all_channels_writes_both_rows(session):
    lead = make_lead(session, email="a@acme.com", phone="+919812345678", consent_whatsapp=True)
    execute(session, lead, [SuppressAllChannels("unsubscribe")], make_deps(), NOW)
    rows = {(r.value, r.kind) for r in session.scalars(select(Suppression))}
    assert rows == {("a@acme.com", "email"), ("+919812345678", "phone")}
    # idempotent
    execute(session, lead, [SuppressAllChannels("unsubscribe"), Suppress("a@acme.com", "email", "x")],
            make_deps(), NOW)
    assert len(session.scalars(select(Suppression)).all()) == 2


def test_suppress_marks_channel_unusable(session):
    from outreach.channels import email_usable, fallback_channel, whatsapp_usable

    lead = make_lead(session, email="b@acme.com", phone="+919812345679", consent_whatsapp=True,
                     verify_status="valid", active_channel="whatsapp")
    assert email_usable(lead) and whatsapp_usable(lead)
    execute(session, lead, [Suppress("b@acme.com", "email", "hard_bounce")], make_deps(), NOW)
    assert lead.verify_status == "invalid" and not email_usable(lead)
    assert fallback_channel(lead) is None
    execute(session, lead, [Suppress("+919812345679", "phone", "wa")], make_deps(), NOW)
    assert lead.consent_whatsapp is False and not whatsapp_usable(lead)


def test_suppress_other_value_leaves_lead_alone(session):
    lead = make_lead(session, email="c@acme.com", verify_status="valid")
    execute(session, lead, [Suppress("other@acme.com", "email", "x")], make_deps(), NOW)
    assert lead.verify_status == "valid"


def test_pause_colleagues(session):
    lead = make_lead(session, email="boss@bigco.com", domain="bigco.com", state=S.ENGAGED)
    c_email = make_lead(session, email="c1@bigco.com", domain="bigco.com", tool_lead_id="T-2",
                        active_channel="email")
    c_wa = make_lead(session, email=None, phone="+919800000001", domain="bigco.com",
                     consent_whatsapp=True, active_channel="whatsapp")
    engaged = make_lead(session, email="c3@bigco.com", domain="bigco.com", state=S.ENGAGED)
    other = make_lead(session, email="x@else.com", domain="else.com")
    deps = make_deps()
    execute(session, lead, [PauseColleagues()], deps, NOW)
    assert c_email.state == "paused" and c_email.pause_reason == "account"
    assert c_wa.state == "paused" and c_wa.pause_reason == "account"
    assert c_wa.variant_flags["wa_reminder_cancelled"] is True
    assert c_email.variant_flags["wa_reminder_cancelled"] is True
    assert engaged.state == "engaged" and other.state == "in_sequence"
    assert lead.state == "engaged"
    assert deps.tool.calls == [("pause", "T-2")]


def test_pause_colleagues_free_mail_domain_noop(session):
    lead = make_lead(session, email="me@gmail.com", domain="gmail.com", state=S.ENGAGED)
    other = make_lead(session, email="you@gmail.com", domain="gmail.com")
    execute(session, lead, [PauseColleagues()], make_deps(), NOW)
    assert other.state == "in_sequence"


def test_wa_and_outreach_dispatch(session):
    lead = make_lead(session)
    deps = make_deps()
    execute(session, lead, [WaSendText("ask_away"), WaSendTemplate("ozoo_reminder"), SendReply("booking_slots"),
                            SwitchChannel("bounce"), RefreshNextStep()], deps, NOW)
    assert deps.wa.calls == [("send_text", lead.id, "ask_away"), ("send_template", lead.id, "ozoo_reminder")]
    assert deps.outreach.calls == [("send_email_text", lead.id, "booking_slots"), ("switch", lead.id, "bounce"),
                                   ("refresh_next_step", lead.id)]


def test_set_variant_sorted_unique_then_refresh(session):
    lead = make_lead(session)
    deps = make_deps()
    execute(session, lead, [SetVariant("product"), SetVariant("case_study"), SetVariant("product")], deps, NOW)
    assert lead.variant_flags["variants"] == ["case_study", "product"]
    assert deps.outreach.names() == ["refresh_next_step"] * 3


def test_score_flag_step_sent(session):
    lead = make_lead(session, score=3, step_sent=2)
    ev = Event(lead_id=lead.id, channel="email", kind="open", occurred_at=NOW - timedelta(hours=1),
               received_at=NOW, data={})
    execute(session, lead, [AddScore(2), SetFlag("human_opens", 1), SetStepSent(1)], make_deps(), NOW, event=ev)
    assert lead.score == 5 and lead.last_human_event_at == NOW - timedelta(hours=1)
    assert lead.variant_flags["human_opens"] == 1
    assert lead.step_sent == 2 and lead.last_sent_at is None  # late step 1 ignored
    execute(session, lead, [SetStepSent(3), AddScore(1)], make_deps(), NOW)
    assert lead.step_sent == 3 and lead.last_sent_at == NOW and lead.last_human_event_at == NOW


def test_alert_goes_to_alerter(session):
    lead = make_lead(session)
    deps = make_deps()
    execute(session, lead, [Alert("hot_lead", "hot")], deps, NOW)
    assert deps.alerter.calls == [("send", "hot_lead", lead.id)]
    assert len(alerts(session, "hot_lead")) == 1


def test_execute_external_failure_continues(session):
    lead = make_lead(session, tool_lead_id="T-1")
    deps = make_deps()
    deps.tool.fail.add("stop")
    deps.wa.fail.add("send_text")
    execute(session, lead, [ToolStop(), WaSendText("ask_away"), SetFlag("done", True), RefreshNextStep()], deps, NOW)
    assert lead.variant_flags["done"] is True
    assert deps.outreach.names() == ["refresh_next_step"]
    errs = alerts(session, "integration_error")
    assert len(errs) == 2
    assert all("RuntimeError" in e.text for e in errs)


def test_alerter_failure_written_directly(session):
    lead = make_lead(session)
    deps = make_deps()
    deps.alerter.fail.add("send")
    execute(session, lead, [Alert("question", "q")], deps, NOW)
    assert len(alerts(session, "integration_error")) == 1


def test_classify_and_botreply_without_deps_alert_unsure(session):
    lead = make_lead(session, state=S.ENGAGED)
    deps = make_deps()
    execute(session, lead, [Classify(), BotReply()], deps, NOW)
    assert [c[1] for c in deps.alerter.calls] == ["unsure", "unsure"]


def test_classify_runs_returned_actions_with_depth_limit(session):
    lead = make_lead(session, state=S.ENGAGED)
    ev = Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW, received_at=NOW, data={})
    seen = []

    def classify(sess, ld, event, now):
        seen.append(event)
        return [SetFlag("classified", len(seen)), Classify()]  # would recurse forever

    deps = make_deps(classify=classify)
    execute(session, lead, [Classify()], deps, NOW, event=ev)
    assert len(seen) == 2  # depth capped at 2
    assert seen[0] is ev
    assert lead.variant_flags["classified"] == 2
    assert len(alerts(session, "integration_error")) == 1


def test_bot_reply_and_handoff(session):
    lead = make_lead(session, state=S.ENGAGED)
    handed = []
    deps = make_deps(bot_reply=lambda s, l, now: [SetFlag("bot", True)],
                     handoff=lambda s, l, now: handed.append(l.id))
    execute(session, lead, [BotReply(), HandOff()], deps, NOW)
    assert lead.variant_flags["bot"] is True and handed == [lead.id]


def test_handoff_without_dep_alerts(session):
    lead = make_lead(session, state=S.BOOKED)
    deps = make_deps()
    execute(session, lead, [HandOff()], deps, NOW)
    assert deps.alerter.calls == [("send", "handoff", lead.id)]


def test_external_db_failure_rolled_back_to_savepoint(session):
    """A port that breaks the DB inside its call must not poison the session."""
    from outreach.models import Lead

    lead = make_lead(session, email="dup@acme.com")
    deps = make_deps()

    def bad_switch(sess, ld, reason, now):
        sess.add(Lead(name="x", company="y", source="z", email="dup@acme.com", created_at=NOW))
        sess.flush()  # IntegrityError

    deps.outreach.switch = bad_switch
    execute(session, lead, [SwitchChannel("bounce"), SetFlag("after", True)], deps, NOW)
    session.flush()
    assert lead.variant_flags["after"] is True
    assert len(alerts(session, "integration_error")) == 1


def test_deps_is_dataclass_with_optional_callables():
    d = make_deps()
    assert isinstance(d, Deps) and d.classify is None and d.bot_reply is None and d.handoff is None


def test_late_sent_for_earlier_step_ignored(session):
    lead = make_lead(session, step_sent=3, last_sent_at=NOW)
    ev = Event(lead_id=lead.id, channel="email", kind="sent", occurred_at=NOW - timedelta(days=5),
               received_at=NOW, data={})
    execute(session, lead, [SetStepSent(2)], make_deps(), NOW, event=ev)
    assert lead.step_sent == 3 and lead.last_sent_at == NOW
