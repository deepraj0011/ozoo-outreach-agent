"""Task 12: worker tick (time-driven jobs), lock, lifespan loop and CLI."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from outreach import worker
from outreach.botfilter import load_scanners
from outreach.db import init_db, make_engine
from outreach.emailtool.base import NormalizedEvent
from outreach.emailtool.fake import FakeEmailTool
from outreach.ingest import ingest
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead, WorkerLock
from outreach.outreach import Outreach
from outreach.states import LeadState
from outreach.whatsapp.fake import FakeWhatsApp

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_deps, make_settings

S = LeadState
PHONE = "+919876543210"


@pytest.fixture
def env(tmp_path):
    # a file database: separate connections, like the real worker next to the web app
    engine = make_engine(f"sqlite:///{tmp_path / 'worker.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = make_settings()
    tool = FakeEmailTool(webhook_secret=WEBHOOK_SECRET)
    wa = FakeWhatsApp(settings)
    outreach = Outreach(settings)
    deps = make_deps(tool=tool, wa=wa, outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(engine=engine, factory=factory, settings=settings, tool=tool, wa=wa, deps=deps,
                          alerter=deps.alerter)
    engine.dispose()


def run(env, now, **kw):
    settings = kw.pop("settings", env.settings)
    return worker.tick(env.factory, env.deps, lambda: now, settings, **kw)


def new_lead(env, **kw):
    with env.factory() as s:
        lead = make_lead(s, **kw)
        s.commit()
        return lead.id


def wa_kw(with_email=False, **kw):
    kw.setdefault("phone", PHONE)
    kw.setdefault("consent_whatsapp", True)
    kw.setdefault("active_channel", "whatsapp")
    if with_email:
        kw.setdefault("verify_status", "valid")
    else:
        kw["email"] = None
    return kw


def get(env, lead_id):
    with env.factory() as s:
        return s.get(Lead, lead_id)


def add(env, *rows):
    with env.factory() as s:
        for r in rows:
            s.add(r)
        s.commit()


def alerts(env, kind=None):
    with env.factory() as s:
        q = select(AlertRow)
        if kind:
            q = q.where(AlertRow.kind == kind)
        return list(s.scalars(q.order_by(AlertRow.id)))


def tool_names(env):
    return [c[0] for c in env.tool.calls]


def templates_sent(env):
    return [c[2] for c in env.wa.calls if c[0] == "send_template"]


def click(lead_id, tag, at, step=1, channel="email", **kw):
    return Event(lead_id=lead_id, channel=channel, kind="click", tag=tag, step=step, occurred_at=at,
                 received_at=at, processed=False, data={}, **kw)


# ---------------------------------------------------------------- start_new

def test_start_new_starts_each_lead_once(env):
    e = new_lead(env, verify_status="valid", active_channel="email")
    w = new_lead(env, **wa_kw())
    out = run(env, NOW)
    assert out["start_new"] == 2
    assert tool_names(env) == ["add_lead"]
    assert templates_sent(env) == ["ozoo_intro"]
    assert get(env, e).tool_lead_id and get(env, w).wa_template_sent_at == NOW
    run(env, NOW + timedelta(minutes=1))
    assert tool_names(env) == ["add_lead"] and templates_sent(env) == ["ozoo_intro"]


def test_start_new_skips_held_and_caps_batch(env):
    new_lead(env, verify_status="valid", active_channel="email", email_hold="eu_approval")
    for _ in range(3):
        new_lead(env, verify_status="valid", active_channel="email")
    assert run(env, NOW, batch=2)["start_new"] == 2
    assert run(env, NOW, batch=2)["start_new"] == 1
    assert tool_names(env) == ["add_lead"] * 3


def test_start_new_retries_failed_enrolment_then_gives_up(env):
    env.tool.fail.add("add_lead")
    lid = new_lead(env, verify_status="valid", active_channel="email")
    t = NOW
    for _ in range(5):
        run(env, t)
        t += timedelta(minutes=30)
    assert tool_names(env).count("add_lead") == worker.START_MAX_ATTEMPTS
    assert get(env, lid).variant_flags["start_failed"]
    assert len(alerts(env, "unsure")) == 1


def test_intro_wa_send_error_falls_back_to_email(env):
    env.wa.client.fail_code = 131026
    lid = new_lead(env, **wa_kw(with_email=True))
    run(env, NOW)
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.channel_switched
    assert tool_names(env) == ["add_lead"]


def test_intro_attempt_is_committed_before_the_send(env):
    seen = {}

    def spy(phone, name, lang="en", variables=None, quick_replies=None):
        with env.factory() as s:  # a separate session sees the committed attempt flag
            seen["flag"] = (s.get(Lead, lid).variant_flags or {}).get("wa_intro_attempted_at")
        return "wamid.X"

    env.wa.client.send_template = spy
    lid = new_lead(env, **wa_kw())
    run(env, NOW)
    assert seen["flag"] == NOW.isoformat()


def test_unconfirmed_intro_matched_by_message_id(env):
    lid = new_lead(env, **wa_kw(variant_flags={"wa_intro_attempted_at": NOW.isoformat()}))
    add(env, ChatMessage(lead_id=lid, direction="out", author="bot", text="[template:ozoo_intro]",
                         wa_message_id="wamid.MINE", created_at=NOW),
        # a status for some other message to this lead comes first: not ours
        Event(lead_id=lid, channel="whatsapp", kind="wa_delivered", occurred_at=NOW + timedelta(seconds=1),
              received_at=NOW, verdict="n/a", processed=True, data={"wamid": "wamid.OTHER"}),
        Event(lead_id=lid, channel="whatsapp", kind="wa_sent", occurred_at=NOW + timedelta(seconds=9),
              received_at=NOW, verdict="n/a", processed=True, data={"wamid": "wamid.MINE"}))
    run(env, NOW + timedelta(minutes=5))
    assert get(env, lid).wa_template_sent_at == NOW + timedelta(seconds=9)


def test_unconfirmed_intro_reconciled_from_status_webhook(env):
    flags = {"wa_intro_attempted_at": NOW.isoformat()}
    a = new_lead(env, **wa_kw(variant_flags=dict(flags)))
    b = new_lead(env, **wa_kw(phone="+919876543211", variant_flags=dict(flags)))
    add(env, Event(lead_id=a, channel="whatsapp", kind="wa_sent", occurred_at=NOW + timedelta(seconds=2),
                   received_at=NOW, verdict="n/a", processed=True, data={}))
    run(env, NOW + timedelta(minutes=5))
    assert get(env, a).wa_template_sent_at == NOW + timedelta(seconds=2)
    assert get(env, b).wa_template_sent_at is None and alerts(env, "wa_send_failed") == []
    run(env, NOW + timedelta(hours=2))
    run(env, NOW + timedelta(hours=3))
    assert len(alerts(env, "wa_send_failed")) == 1
    assert templates_sent(env) == []  # never resent


# ---------------------------------------------------------------- clicks

def test_click_not_processed_before_30s(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "case_study", NOW))
    run(env, NOW + timedelta(seconds=10))
    with env.factory() as s:
        ev = s.scalars(select(Event).where(Event.kind == "click")).one()
        assert ev.processed is False and ev.verdict is None
    run(env, NOW + timedelta(seconds=31))
    with env.factory() as s:
        ev = s.scalars(select(Event).where(Event.kind == "click")).one()
        assert ev.processed is True and ev.verdict == "human"
    assert get(env, lid).score == 5


def test_two_quick_clicks_both_bot_after_settle(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "pricing", NOW), click(lid, "case_study", NOW + timedelta(seconds=3)))
    run(env, NOW + timedelta(minutes=1))
    with env.factory() as s:
        verdicts = s.scalars(select(Event.verdict).where(Event.kind == "click")).all()
    assert verdicts == ["bot", "bot"]
    assert get(env, lid).score == 0


def test_click_soon_after_step_send_is_bot(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", step_sent=2,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, Event(lead_id=lid, channel="email", kind="sent", step=1, occurred_at=NOW - timedelta(seconds=20),
                   received_at=NOW, verdict="n/a", processed=True, data={}),
        click(lid, "homepage", NOW, step=1))
    run(env, NOW + timedelta(minutes=1))
    with env.factory() as s:
        assert s.scalars(select(Event.verdict).where(Event.kind == "click")).one() == "bot"


def no_check(env):
    """CLICK_ALERTS_NEED_CHECK off: clicks act on their own (the behaviour before the trial safety)."""
    return env.settings.model_copy(update={"click_alerts_need_check": False})


def test_booking_click_not_booked_sends_slots_once(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "booking", NOW))
    run(env, NOW + timedelta(minutes=1), settings=no_check(env))  # settles the click (+10)
    assert get(env, lid).score == 10
    run(env, NOW + timedelta(minutes=29), settings=no_check(env))
    assert "reply" not in tool_names(env)
    run(env, NOW + timedelta(minutes=31), settings=no_check(env))
    run(env, NOW + timedelta(minutes=45), settings=no_check(env))
    assert tool_names(env).count("reply") == 1


def test_booking_click_alone_sends_no_email_while_clicks_need_a_check(env):
    """Task 19: a bank sandbox clicking the booking link must not make us email the lead."""
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "booking", NOW))
    run(env, NOW + timedelta(minutes=1))
    run(env, NOW + timedelta(minutes=31))
    assert "reply" not in tool_names(env) and get(env, lid).score == 10


def test_booking_click_after_a_verified_visit_still_nudges(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1), variant_flags={"verified_visit": True})
    add(env, click(lid, "booking", NOW))
    run(env, NOW + timedelta(minutes=1))
    run(env, NOW + timedelta(minutes=31))
    assert tool_names(env).count("reply") == 1


def test_booking_click_then_booked_sends_nothing(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "booking", NOW))
    run(env, NOW + timedelta(minutes=1))
    add(env, Event(lead_id=lid, channel="cal", kind="booking_created", occurred_at=NOW + timedelta(minutes=5),
                   received_at=NOW, verdict="n/a", processed=True, data={}))
    run(env, NOW + timedelta(minutes=40))
    assert "reply" not in tool_names(env)


def test_booking_click_on_whatsapp_with_closed_window_alerts(env):
    lid = new_lead(env, **wa_kw(wa_template_sent_at=NOW - timedelta(hours=1),
                                variant_flags={"wa_intro_attempted_at": "x"}))
    add(env, click(lid, "booking", NOW, channel="whatsapp", user_agent="Mozilla/5.0 (iPhone)"))
    run(env, NOW + timedelta(minutes=1), settings=no_check(env))
    run(env, NOW + timedelta(minutes=31), settings=no_check(env))
    assert len(alerts(env, "wa_human_needed")) == 1
    assert [c for c in env.wa.calls if c[0] != "send_template"] == []


# ---------------------------------------------------------------- WhatsApp reminder / no response

def wa_started(env, sent_at, **kw):
    flags = {"wa_intro_attempted_at": sent_at.isoformat(), **kw.pop("flags", {})}
    return new_lead(env, **wa_kw(wa_template_sent_at=sent_at, variant_flags=flags, **kw))


def test_wa_reminder_at_48h(env):
    lid = wa_started(env, NOW)
    run(env, NOW + timedelta(hours=47))
    assert templates_sent(env) == []
    run(env, NOW + timedelta(hours=48))
    run(env, NOW + timedelta(hours=49))
    assert templates_sent(env) == ["ozoo_reminder"]
    assert get(env, lid).wa_reminder_sent_at == NOW + timedelta(hours=48)


def test_wa_reminder_send_error_goes_through_decide(env):
    lid = wa_started(env, NOW, with_email=True)
    env.wa.client.fail_code = 131049  # marketing limit
    run(env, NOW + timedelta(hours=48))
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.channel_switched
    assert tool_names(env) == ["add_lead"]
    run(env, NOW + timedelta(hours=49))
    assert len([c for c in env.wa.calls if c[0] == "send_template"]) == 1


def test_wa_reminder_error_without_fallback_alerts(env):
    lid = wa_started(env, NOW)
    env.wa.client.fail_code = 999
    run(env, NOW + timedelta(hours=48))
    assert len(alerts(env, "wa_send_failed")) == 1
    assert get(env, lid).state == S.NURTURE.value  # switch_channel at once: no fallback -> nurture


def test_wa_reminder_unhandled_error_switches_to_email_at_once(env):
    lid = wa_started(env, NOW, with_email=True)
    env.wa.client.fail_code = 999  # decide only alerts for this code
    run(env, NOW + timedelta(hours=48))
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.channel_switched
    assert tool_names(env) == ["add_lead"]


def test_wa_reminder_not_allowed_switches_to_email_at_once(env):
    lid = wa_started(env, NOW, with_email=True)
    with env.factory() as s:
        s.get(Lead, lid).consent_whatsapp = False
        s.commit()
    run(env, NOW + timedelta(hours=48))
    lead = get(env, lid)
    assert lead.active_channel == "email" and tool_names(env) == ["add_lead"]
    assert templates_sent(env) == []


def test_switched_lead_with_failed_enrolment_retried_next_tick(env):
    lid = wa_started(env, NOW - timedelta(hours=96), with_email=True, wa_reminder_sent_at=NOW - timedelta(hours=48))
    env.tool.fail.add("add_lead")
    run(env, NOW)
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.tool_lead_id is None
    env.tool.fail.clear()
    run(env, NOW + timedelta(minutes=1))
    assert tool_names(env) == ["add_lead", "find_lead", "add_lead"]  # A4: looked up before re-adding
    assert get(env, lid).tool_lead_id is not None


def test_reply_before_reminder_cancels_it(env):
    lid = wa_started(env, NOW)
    with env.factory() as s:
        ingest(s, [NormalizedEvent(provider_event_id="wamid.in1", channel="whatsapp", kind="wa_text",
                                   occurred_at=NOW + timedelta(hours=10), phone=PHONE, data={"text": "hi"})],
               env.deps, NOW + timedelta(hours=10), load_scanners())
    run(env, NOW + timedelta(hours=48))
    run(env, NOW + timedelta(hours=100))
    assert templates_sent(env) == []
    assert get(env, lid).state == S.ENGAGED.value


def test_wa_no_response_switches_to_email(env):
    lid = wa_started(env, NOW - timedelta(hours=48), with_email=True, wa_reminder_sent_at=NOW - timedelta(hours=1))
    run(env, NOW + timedelta(hours=46))
    assert get(env, lid).active_channel == "whatsapp"
    run(env, NOW + timedelta(hours=47))
    lead = get(env, lid)
    assert lead.active_channel == "email" and lead.channel_switched
    assert tool_names(env) == ["add_lead"]


def test_wa_no_response_no_email_goes_nurture(env):
    lid = wa_started(env, NOW - timedelta(hours=96), wa_reminder_sent_at=NOW - timedelta(hours=48))
    run(env, NOW)
    assert get(env, lid).state == S.NURTURE.value
    assert tool_names(env) == []


# ---------------------------------------------------------------- holds

def test_ooo_resumes_on_date(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", state=S.PAUSED,
                   pause_reason="ooo", pause_until=NOW + timedelta(days=3))
    run(env, NOW + timedelta(days=2))
    assert get(env, lid).state == S.PAUSED.value
    run(env, NOW + timedelta(days=3))
    lead = get(env, lid)
    assert lead.state == S.IN_SEQUENCE.value and lead.pause_reason is None and lead.pause_until is None


def test_ooo_resume_re_enrols_at_the_next_step(env):
    """The reply that carried the out-of-office stopped (deleted) the tool lead: re-enrol it, with the
    campaign's step 1 carrying our step 3 (step_sent was 2)."""
    from outreach.sequencer import render_step

    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-old", step_sent=2,
                   state=S.PAUSED, pause_reason="ooo", pause_until=NOW)
    run(env, NOW + timedelta(minutes=1))
    lead = get(env, lid)
    assert lead.state == S.IN_SEQUENCE.value
    assert "resume" not in tool_names(env)
    (call,) = [c for c in env.tool.calls if c[0] == "add_lead"]
    variables = call[3]
    real3 = render_step(lead, 3, settings=env.settings)
    real4 = render_step(lead, 4, settings=env.settings)
    assert variables["subject_1"] == real3["subject_3"] and variables["body_1"] == real3["body_3"]
    assert variables["subject_2"] == real4["subject_4"]
    assert lead.tool_lead_id not in (None, "t-old")
    assert lead.variant_flags["step_offset"] == 2
    assert lead.variant_flags["previous_tool_lead_id"] == "t-old"
    run(env, NOW + timedelta(minutes=2))
    assert tool_names(env).count("add_lead") == 1


def test_ooo_resume_after_last_step_does_not_re_enrol(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-old", step_sent=4,
                   last_sent_at=NOW, state=S.PAUSED, pause_reason="ooo", pause_until=NOW)
    run(env, NOW + timedelta(minutes=1))
    assert get(env, lid).state == S.IN_SEQUENCE.value
    assert "add_lead" not in tool_names(env)


def test_ooo_resume_whatsapp_lead_does_not_touch_the_email_tool(env):
    lid = wa_started(env, NOW - timedelta(hours=1), state=S.PAUSED, pause_reason="ooo", pause_until=NOW)
    run(env, NOW + timedelta(minutes=1))
    assert get(env, lid).state == S.IN_SEQUENCE.value
    assert env.tool.calls == []


def test_tick_resumes_account_holds(env):
    blocker = new_lead(env, email="a@corp.io", domain="corp.io", state=S.ARCHIVED)
    lid = new_lead(env, email="b@corp.io", domain="corp.io", verify_status="valid", active_channel="email",
                   state=S.PAUSED, pause_reason="account", variant_flags={"paused_by": blocker})
    run(env, NOW)
    assert get(env, lid).state == S.IN_SEQUENCE.value


def test_tick_releases_eu_holds_only_when_approved(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", state=S.PAUSED, pause_reason="eu_approval",
                   email_hold="eu_approval", is_eu=True)
    run(env, NOW)
    assert get(env, lid).email_hold == "eu_approval"
    run(env, NOW + timedelta(minutes=1), settings=make_settings(eu_email_approved=True))
    lead = get(env, lid)
    assert lead.email_hold is None and lead.state == S.IN_SEQUENCE.value
    run(env, NOW + timedelta(minutes=2), settings=make_settings(eu_email_approved=True))
    assert tool_names(env) == ["add_lead"]


# ---------------------------------------------------------------- end of sequence

def test_end_of_sequence_after_5_days(env):
    quiet = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=4,
                     last_sent_at=NOW)
    clicked = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-2", step_sent=4,
                       last_sent_at=NOW)
    add(env, Event(lead_id=clicked, channel="email", kind="click", tag="homepage", step=2,
                   occurred_at=NOW - timedelta(days=5), received_at=NOW, verdict="human", processed=True, data={}))
    run(env, NOW + timedelta(days=4, hours=23))
    assert get(env, quiet).state == S.IN_SEQUENCE.value
    run(env, NOW + timedelta(days=5))
    q, c = get(env, quiet), get(env, clicked)
    assert (q.state, q.nurture_track) == (S.NURTURE.value, "monthly")
    assert (c.state, c.nurture_track) == (S.NURTURE.value, "warm_2_3w")


def test_end_of_sequence_hot_lead_alerts(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=4,
                   last_sent_at=NOW, score=20, last_human_event_at=NOW)
    run(env, NOW + timedelta(days=5))
    assert get(env, lid).nurture_track == "sales_personal"
    assert len(alerts(env, "click_check")) == 1  # task 18: score from clicks only
    assert alerts(env, "hot_no_reply") == []


def test_end_of_sequence_hot_lead_alerts_sales_when_check_off(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=4,
                   last_sent_at=NOW, score=20, last_human_event_at=NOW)
    run(env, NOW + timedelta(days=5), settings=env.settings.model_copy(update={"click_alerts_need_check": False}))
    assert get(env, lid).nurture_track == "sales_personal"
    assert len(alerts(env, "hot_no_reply")) == 1 and alerts(env, "click_check") == []


# ---------------------------------------------------------------- WhatsApp SLA

FRI_1730_IST = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)  # Friday 17:30 Asia/Kolkata
MON_0930_IST = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)


def takeover_lead(env, at, phone=PHONE):
    return new_lead(env, **wa_kw(state=S.ENGAGED, wa_human_owner=None, phone=phone,
                                 variant_flags={"takeover_at": at.isoformat(), "takeover_reason": "unsure"}))


def test_business_deadline():
    hours = worker.parse_business_hours("Mon-Fri 09:00-18:00")
    tz = worker.business_zone("Asia/Kolkata")
    assert worker.business_deadline(FRI_1730_IST, timedelta(hours=1), hours, tz) == MON_0930_IST
    tue_10 = datetime(2026, 11, 17, 4, 30, tzinfo=timezone.utc)
    assert worker.business_deadline(tue_10, timedelta(hours=1), hours, tz) == tue_10 + timedelta(hours=1)
    sat = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)
    assert worker.business_deadline(sat, timedelta(hours=1), hours, tz) == MON_0930_IST + timedelta(minutes=30)
    with pytest.raises(ValueError):
        worker.parse_business_hours("weekdays 9-6")


def test_wa_sla_alert_after_1_business_hour(env):
    takeover_lead(env, FRI_1730_IST)
    run(env, FRI_1730_IST + timedelta(hours=1))  # Friday 18:30: not yet
    run(env, MON_0930_IST - timedelta(minutes=1))
    assert alerts(env, "wa_sla_missed") == []
    run(env, MON_0930_IST)
    run(env, MON_0930_IST + timedelta(hours=3))
    assert len(alerts(env, "wa_sla_missed")) == 1


def test_wa_sla_no_alert_when_a_human_replied(env):
    lid = takeover_lead(env, FRI_1730_IST)
    add(env, ChatMessage(lead_id=lid, direction="out", author="human", text="hello",
                         created_at=FRI_1730_IST + timedelta(minutes=10)))
    run(env, MON_0930_IST + timedelta(hours=1))
    assert alerts(env, "wa_sla_missed") == []


def test_wa_sla_re_alert_on_new_inbound_rate_limited(env):
    lid = takeover_lead(env, FRI_1730_IST)
    run(env, MON_0930_IST)
    assert len(alerts(env, "wa_sla_missed")) == 1
    add(env, ChatMessage(lead_id=lid, direction="in", author="lead", text="anyone?",
                         created_at=MON_0930_IST + timedelta(minutes=5)))
    run(env, MON_0930_IST + timedelta(minutes=10))
    assert len(alerts(env, "wa_sla_missed")) == 1  # rate-limited
    run(env, MON_0930_IST + timedelta(minutes=30))
    assert len(alerts(env, "wa_sla_missed")) == 2
    run(env, MON_0930_IST + timedelta(minutes=70))
    assert len(alerts(env, "wa_sla_missed")) == 2  # nothing new from the lead


def test_wa_sla_after_human_reply_new_inbound_starts_a_new_clock(env):
    lid = takeover_lead(env, MON_0930_IST)
    add(env, ChatMessage(lead_id=lid, direction="out", author="human", text="hi",
                         created_at=MON_0930_IST + timedelta(minutes=5)),
        ChatMessage(lead_id=lid, direction="in", author="lead", text="question",
                    created_at=MON_0930_IST + timedelta(minutes=20)))
    run(env, MON_0930_IST + timedelta(minutes=79))
    assert alerts(env, "wa_sla_missed") == []
    run(env, MON_0930_IST + timedelta(minutes=80))
    assert len(alerts(env, "wa_sla_missed")) == 1


def test_wa_sla_released_takeover_not_alerted(env):
    new_lead(env, **wa_kw(state=S.ENGAGED, variant_flags={"takeover_at": FRI_1730_IST.isoformat(),
                                                          "takeover_released": "x"}))
    run(env, MON_0930_IST + timedelta(hours=2))
    assert alerts(env, "wa_sla_missed") == []


# ---------------------------------------------------------------- attended check

def test_attended_check_hands_off_after_2h(env):
    end = NOW
    lid = new_lead(env, verify_status="valid", active_channel="email", state=S.BOOKED,
                   variant_flags={"booking_end": "2026-11-12T10:00:00Z", "booking_uid": "u1"})
    run(env, end + timedelta(hours=2))
    assert get(env, lid).state == S.BOOKED.value
    run(env, end + timedelta(hours=2, minutes=1))
    run(env, end + timedelta(hours=3))
    assert get(env, lid).state == S.HANDED_OFF.value
    assert len(alerts(env, "handoff")) == 1


def test_attended_check_ignores_bad_end(env):
    lid = new_lead(env, verify_status="valid", active_channel="email", state=S.BOOKED,
                   variant_flags={"booking_end": "garbage"})
    run(env, NOW + timedelta(days=1))
    assert get(env, lid).state == S.BOOKED.value


# ---------------------------------------------------------------- decay / jobs / alerts

def test_decay_scores(env):
    lid = new_lead(env, score=20, last_human_event_at=NOW - timedelta(days=31))
    fresh = new_lead(env, score=20, last_human_event_at=NOW - timedelta(days=3))
    run(env, NOW)
    run(env, NOW)
    assert get(env, lid).score == 10 and get(env, fresh).score == 20


def test_lock_renewed_inside_run_due(env, monkeypatch):
    clock = {"now": NOW}
    seen = {}

    def fake_run_due(factory, deps, now_fn, **kw):
        clock["now"] = NOW + timedelta(minutes=14)
        kw["after_each"]()
        with env.factory() as s:
            seen["expires"] = s.get(WorkerLock, worker.LOCK_NAME).expires_at
        return 1

    monkeypatch.setattr(worker.jobs, "run_due", fake_run_due)
    worker.tick(env.factory, env.deps, lambda: clock["now"], env.settings)
    assert seen["expires"] == NOW + timedelta(minutes=14) + worker.LOCK_LEASE


def test_lock_renewed_per_lead_within_a_job(env, monkeypatch):
    for _ in range(3):
        new_lead(env, verify_status="valid", active_channel="email")
    renewals = []
    real_acquire = worker._acquire

    def spy_acquire(factory, now_fn):
        lock = real_acquire(factory, now_fn)
        real_renew = lock.renew
        lock.renew = lambda: (renewals.append(1), real_renew())
        return lock

    monkeypatch.setattr(worker, "_acquire", spy_acquire)
    monkeypatch.setattr(worker, "JOBS", (("start_new", worker._start_new),))
    run(env, NOW)
    assert len(renewals) >= 3


def test_run_due_jobs_called(env, monkeypatch):
    seen = []
    monkeypatch.setattr(worker.jobs, "run_due", lambda f, d, n, **kw: seen.append(n()) or 3)
    assert run(env, NOW)["run_due_jobs"] == 3
    assert seen == [NOW]


def test_retry_alerts_reposts_unposted(env):
    lid = new_lead(env)
    rows = [AlertRow(lead_id=lid, kind="hot_lead", text="t", created_at=NOW - timedelta(minutes=2))
            for _ in range(22)]
    rows.append(AlertRow(lead_id=lid, kind="hot_lead", text="new", created_at=NOW - timedelta(seconds=30)))
    add(env, *rows)
    run(env, NOW)
    assert env.alerter.names().count("repost") == 20
    run(env, NOW + timedelta(minutes=1))
    assert env.alerter.names().count("repost") == 23
    run(env, NOW + timedelta(minutes=2))
    assert env.alerter.names().count("repost") == 23
    assert all(a.posted_at is not None for a in alerts(env, "hot_lead"))


def test_failed_repost_stays_unposted(env):
    add(env, AlertRow(lead_id=None, kind="hot_lead", text="t", created_at=NOW - timedelta(minutes=2)))
    env.alerter.repost_ok = False
    run(env, NOW)
    assert alerts(env, "hot_lead")[0].posted_at is None


def test_integration_error_digest_hourly(env):
    add(env, *[AlertRow(lead_id=None, kind="integration_error", text=f"lead {i}: tool.stop failed",
                        created_at=NOW - timedelta(minutes=5 + 8 - i)) for i in range(1, 8)])
    run(env, NOW)
    digests = [c for c in env.alerter.calls if c[0] == "post_text"]
    assert len(digests) == 1
    text = digests[0][2]
    assert "7 " in text and "lead 7" in text and "lead 2" not in text  # latest 5 only
    assert "repost" not in env.alerter.names()
    assert all(a.posted_at is not None for a in alerts(env, "integration_error"))
    add(env, AlertRow(lead_id=None, kind="integration_error", text="x", created_at=NOW))
    run(env, NOW + timedelta(minutes=30))
    assert len([c for c in env.alerter.calls if c[0] == "post_text"]) == 1
    run(env, NOW + timedelta(minutes=61))
    assert len([c for c in env.alerter.calls if c[0] == "post_text"]) == 2


def test_check_mailboxes_runs_daily(env, monkeypatch):
    seen = []
    monkeypatch.setattr(worker, "check_mailboxes", lambda s, tool, alerter, now: seen.append(now) or [])
    run(env, NOW)
    run(env, NOW + timedelta(hours=23))
    run(env, NOW + timedelta(hours=24))
    assert seen == [NOW, NOW + timedelta(hours=24)]


# ---------------------------------------------------------------- tick robustness

def test_settled_click_failing_three_times_is_closed_as_error(env, monkeypatch):
    lid = new_lead(env, verify_status="valid", active_channel="email", step_sent=1,
                   last_sent_at=NOW - timedelta(days=1))
    add(env, click(lid, "case_study", NOW))

    def boom(*a, **k):
        raise RuntimeError("decide broke")

    monkeypatch.setattr(worker, "decide", boom)
    for i in range(1, 4):
        run(env, NOW + timedelta(minutes=i))
        with env.factory() as s:
            ev = s.scalars(select(Event).where(Event.kind == "click")).one()
        if i < 3:
            assert ev.processed is False and ev.data["settle_failures"] == i
    assert ev.processed is True and ev.verdict == "error"
    run(env, NOW + timedelta(minutes=5))
    assert len(alerts(env, "integration_error")) == 3


def test_tick_stops_between_jobs(env, monkeypatch):
    stop = threading.Event()
    ran = []

    def first(ctx, session):
        ran.append("first")
        stop.set()
        return 0

    def second(ctx, session):
        ran.append("second")
        return 0

    monkeypatch.setattr(worker, "JOBS", (("first", first), ("second", second)))
    out = worker.tick(env.factory, env.deps, lambda: NOW, env.settings, stop=stop)
    assert ran == ["first"] and out["stopped"] == 1
    with env.factory() as s:
        assert s.scalars(select(WorkerLock)).all() == []


def test_pg_unlock_failure_invalidates_the_connection():
    class Conn:
        invalidated = closed = False

        def execute(self, *a, **k):
            raise RuntimeError("connection lost")

        def invalidate(self):
            self.invalidated = True

        def close(self):
            self.closed = True

    conn = Conn()
    worker._pg_release(conn)
    assert conn.invalidated and conn.closed


def test_failing_job_does_not_block_later_jobs(env, monkeypatch):
    def boom(ctx, session):
        raise RuntimeError("secret detail lead1@acme.com")

    monkeypatch.setattr(worker, "JOBS", (("boom", boom), *worker.JOBS))
    lid = new_lead(env, verify_status="valid", active_channel="email")
    out = run(env, NOW)
    assert out["boom"] == -1 and out["failed_jobs"] == 1
    assert get(env, lid).tool_lead_id is not None
    errs = alerts(env, "integration_error")
    assert len(errs) == 1 and "RuntimeError" in errs[0].text and "acme" not in errs[0].text


def test_tick_is_idempotent(env):
    new_lead(env, verify_status="valid", active_channel="email")
    wa_started(env, NOW - timedelta(hours=48))
    takeover_lead(env, NOW - timedelta(days=3), phone="+919876543299")
    new_lead(env, verify_status="valid", active_channel="email", state=S.BOOKED,
             variant_flags={"booking_end": (NOW - timedelta(hours=3)).isoformat()})
    run(env, NOW)
    assert {a.kind for a in alerts(env)} >= {"wa_sla_missed", "handoff"}
    assert tool_names(env) == ["add_lead"]
    calls = (list(env.tool.calls), list(env.wa.calls), list(env.alerter.calls))
    n_alerts = len(alerts(env))
    run(env, NOW)
    assert (env.tool.calls, env.wa.calls, env.alerter.calls) == calls
    assert len(alerts(env)) == n_alerts
    assert templates_sent(env) == ["ozoo_reminder"]


def test_second_concurrent_tick_skips(env):
    add(env, WorkerLock(name=worker.LOCK_NAME, owner="other", expires_at=NOW + timedelta(minutes=5)))
    new_lead(env, verify_status="valid", active_channel="email")
    assert run(env, NOW) == {"skipped": 1}
    assert env.tool.calls == []
    out = run(env, NOW + timedelta(minutes=6))  # the other worker's lease expired
    assert out.get("start_new") == 1
    with env.factory() as s:
        assert s.scalars(select(WorkerLock)).all() == []  # released


def test_lock_is_held_during_tick(env, monkeypatch):
    seen = {}

    def probe(ctx, session):
        seen["second"] = worker.tick(env.factory, env.deps, lambda: NOW, env.settings)
        return 0

    monkeypatch.setattr(worker, "JOBS", (("probe", probe),))
    run(env, NOW)
    assert seen["second"] == {"skipped": 1}


def test_no_pii_in_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    secret_email, secret_phone, secret_name = "zed.secret@hidden-corp.com", "+919812345678", "Zedediah Hush"
    lid = new_lead(env, email=secret_email, name=secret_name, phone=secret_phone, consent_whatsapp=True,
                   verify_status="valid", active_channel="whatsapp", domain="hidden-corp.com")
    run(env, NOW)
    add(env, click(lid, "pricing", NOW + timedelta(minutes=1), channel="whatsapp", user_agent="Mozilla"))
    env.wa.client.fail_code = 131026
    run(env, NOW + timedelta(hours=48))
    run(env, NOW + timedelta(days=30))
    for secret in (secret_email, secret_phone, secret_name, "hidden-corp", "9812345678"):
        assert secret not in caplog.text


# ---------------------------------------------------------------- running the worker

def test_load_scanners_default_path_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_scanners().ua_substrings


def test_lifespan_loop_only_with_run_worker(env, monkeypatch):
    from outreach.app import create_app

    ticked = threading.Event()

    stops = []

    def fake_tick(factory, deps, now_fn, settings, **kw):
        stops.append(kw.get("stop"))
        ticked.set()
        return {}

    monkeypatch.setattr(worker, "tick", fake_tick)
    app = create_app(env.settings, env.deps, env.engine, scanners=load_scanners(), verifier=object())
    with TestClient(app):
        time.sleep(0.2)
    assert not ticked.is_set()

    settings = make_settings(run_worker=True, worker_interval_s=0.05)
    app = create_app(settings, env.deps, env.engine, scanners=load_scanners(), verifier=object())
    with TestClient(app):
        assert ticked.wait(2)
        task = app.state.worker_task
    assert task.done()  # stopped cleanly on shutdown
    assert stops and all(isinstance(st, threading.Event) and st.is_set() for st in stops)


def test_lifespan_loop_survives_tick_errors(env, monkeypatch):
    from outreach.app import create_app

    calls = []

    def bad_tick(*a, **kw):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "tick", bad_tick)
    settings = make_settings(run_worker=True, worker_interval_s=0.02)
    app = create_app(settings, env.deps, env.engine, scanners=load_scanners(), verifier=object())
    with TestClient(app):
        deadline = time.time() + 2
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.02)
    assert len(calls) >= 2


def test_cli_once_runs_a_tick(env):
    lid = new_lead(env, verify_status="valid", active_channel="email")
    assert worker.main(["--once"], settings=env.settings, deps=env.deps, engine=env.engine) == 0
    assert get(env, lid).tool_lead_id is not None


def test_cli_requires_a_mode(env):
    with pytest.raises(SystemExit):
        worker.main([], settings=env.settings, deps=env.deps, engine=env.engine)


def test_end_of_sequence_verified_visit_alerts_sales_even_with_check_on(env):
    """Task 19: a hot score backed by a verified page visit is a real person, not a scanner."""
    lid = new_lead(env, verify_status="valid", active_channel="email", tool_lead_id="t-1", step_sent=4,
                   last_sent_at=NOW, score=20, last_human_event_at=NOW, variant_flags={"verified_visit": True})
    run(env, NOW + timedelta(days=5))
    assert len(alerts(env, "hot_no_reply")) == 1 and alerts(env, "click_check") == []
