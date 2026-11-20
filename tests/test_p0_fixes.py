"""P0 scheduled emails, final-review fix wave: replies, Send now, stale starts, stuck emails, schedule edits,
account holds, lead controls and the smaller display fixes."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from outreach import jobs, worker
from outreach.actions import Alert, Classify, SendReply, ToolPause, execute
from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.classifier import ReplyClass, actions_for_class
from outreach.emailtool.mailpit import MAX_STEPS, MailpitEmailTool
from outreach.holds import resume_account_holds
from outreach.models import Alert as AlertRow
from outreach.models import Event, Job, Lead
from outreach.outreach import EMAIL_ENROLL_ATTEMPT, start_outreach
from outreach.p0 import flow, planner
from outreach.p0 import schedule as sch
from outreach.p0.templates import P0Error
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.sendwindow import window_from_settings
from outreach.states import LeadState

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_app_engine, make_deps, make_settings
from .test_p0_flow import (S, SMTPStub, _reset_smtp, clock, env, event, rows, set_schedule, start,  # noqa: F401
                           tool, tool_status, tool_vars)

HOUR = timedelta(hours=1)
IST = ZoneInfo("Asia/Kolkata")
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())


def _after_email_1(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    tool.send_due({})
    deps = make_deps(tool=tool)
    event(session, lead, "sent", NOW, deps, step=1)
    return lead, deps


def _rc(label):
    return ReplyClass(label=label, confidence=0.9, return_date=None, later_date=None, referral_email=None,
                      referral_phone=None, referral_name=None, reason="r", draft_answer=None)


def _reply_event(session, lead, at, **data):
    ev = Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=at, received_at=at, verdict="human",
               processed=True, data={"text": "hello", **data})
    session.add(ev)
    session.flush()
    return ev


def _alerts(session, kind=None):
    q = select(func.count(AlertRow.id))
    if kind:
        q = q.where(AlertRow.kind == kind)
    return session.scalar(q)


# ------------------------------------------------------------------ 1. second reply never gets an email

@pytest.mark.parametrize("label", ["interested", "question", "not_now"])
def test_engaged_p0_lead_reply_gets_one_alert_no_email(session, tool, label):
    lead, deps = _after_email_1(session, tool)
    lead.state = LeadState.ENGAGED.value

    acts = actions_for_class(_rc(label), lead, _reply_event(session, lead, NOW + HOUR), NOW + HOUR)

    assert not any(isinstance(a, SendReply) for a in acts)
    assert [type(a) for a in acts] == [Alert] and acts[0].kind == "engaged"


def test_p0_colleague_reply_alert_names_company(session, tool):
    lead, deps = _after_email_1(session, tool)

    acts = actions_for_class(_rc("question"), lead, _reply_event(session, lead, NOW + HOUR, matched_by="domain"),
                             NOW + HOUR)

    alert = next(a for a in acts if isinstance(a, Alert))
    assert alert.text == (f"a colleague at Acme replied to lead {lead.id}'s email; emails stopped, "
                          "please answer personally")
    execute(session, lead, acts, deps, NOW + HOUR)
    assert lead.state == LeadState.ENGAGED.value and rows(session, lead)[1].status == "cancelled"


# ------------------------------------------------------------------ 2. Send now refuses instead of double-sending

def test_send_now_refuses_when_an_email_is_waiting_and_changes_nothing(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    before = (rows(session, lead)[1].due_at, rows(session, lead)[1].reason)

    with pytest.raises(P0Error, match="waiting in the sender"):
        flow.send_now(session, lead, tool, "pm", NOW + HOUR, settings=S)

    r2 = rows(session, lead)[1]
    assert (r2.due_at, r2.reason, r2.status) == (*before, "planned")


def test_send_now_refuses_while_a_reply_is_read(session, tool):
    lead, _ = _after_email_1(session, tool)
    session.add(Job(lead_id=lead.id, kind="classify", dedupe_key="c1", status="pending", run_after=NOW,
                    created_at=NOW, attempts=0))
    session.flush()

    with pytest.raises(P0Error, match="reply"):
        flow.send_now(session, lead, tool, "pm", NOW + HOUR, settings=S)
    assert rows(session, lead)[1].reason is None


# ------------------------------------------------------------------ 3. a reply pauses the sender at once

def test_reply_pauses_sender_before_classify(session, tool):
    lead, deps = _after_email_1(session, tool)
    ev = _reply_event(session, lead, NOW + HOUR)

    acts = decide(LeadSnapshot.from_lead(lead, NOW + HOUR, clicks_need_check=True), EventSnapshot.from_event(ev),
                  NOW + HOUR)
    assert acts == [ToolPause(), Classify()]
    execute(session, lead, acts, deps, NOW + HOUR, event=ev)

    assert tool_status(tool, lead) == "paused" and lead.pause_until is None


def test_ooo_reply_resumes_sender_and_keeps_schedule(session, tool):
    lead, deps = _after_email_1(session, tool)
    ev = event(session, lead, "reply", NOW + HOUR, deps, data={"text": "away"})
    assert tool_status(tool, lead) == "paused"

    execute(session, lead, actions_for_class(_rc("ooo"), lead, ev, NOW + HOUR), deps, NOW + HOUR, event=ev)

    assert tool_status(tool, lead) == "active"
    assert [r.status for r in rows(session, lead)] == ["sent", "planned", "planned", "planned"]


def test_ooo_reply_keeps_pm_pause(session, tool):
    lead, deps = _after_email_1(session, tool)
    flow.pause(session, lead, tool, "pm", NOW)
    ev = event(session, lead, "reply", NOW + HOUR, deps, data={"text": "away"})

    execute(session, lead, actions_for_class(_rc("ooo"), lead, ev, NOW + HOUR), deps, NOW + HOUR, event=ev)

    assert tool_status(tool, lead) == "paused"


def test_unreadable_reply_stops_p0_emails(session, tool):
    lead, deps = _after_email_1(session, tool)
    job = Job(lead_id=lead.id, kind="classify", dedupe_key="c2", status="failed", run_after=NOW, created_at=NOW,
              attempts=3)
    session.add(job)
    session.flush()

    jobs._give_up(session, job.id, "classify", lead.id, deps, NOW + HOUR, "3 attempts")

    assert [(r.status, r.reason) for r in rows(session, lead)][1:] == [("cancelled", "reply_unread")] * 3
    assert tool_status(tool, lead) == "stopped" and _alerts(session, "unsure") == 1


# ------------------------------------------------------------------ 4. day 0 is never in the past

def test_stale_import_start_begins_now(session, tool):
    lead = start(session, tool, variant_flags={"plan_start": (NOW - timedelta(days=3)).isoformat()})

    assert rows(session, lead)[0].due_at == NOW


def test_recovered_enrolment_starts_p0_from_now(session, tool):
    lead = make_lead(session, active_channel="email", verify_status="valid",
                     variant_flags={EMAIL_ENROLL_ATTEMPT: NOW.isoformat(),
                                    "plan_start": (NOW - timedelta(days=3)).isoformat()})
    tool.add_lead("c1", lead, {"plan": "1"})

    assert start_outreach(session, lead, make_deps(tool=tool), NOW, settings=S) == "started"

    got = rows(session, lead)
    assert len(got) == 4 and got[0].due_at == NOW  # starter templates made, day 0 = now


# ------------------------------------------------------------------ 5. steps = MAX_STEPS; stuck "handed" alert

def test_tool_steps_is_max_at_enrol_and_handoff(session, tool):
    set_schedule(session, [0])
    lead = start(session, tool)
    assert tool_vars(tool, lead)["steps"] == str(MAX_STEPS)

    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)

    v = tool_vars(tool, lead)
    assert v["steps"] == str(MAX_STEPS) and v["p0"] == "1"


def test_stuck_handed_email_alerts_once(env, clock):
    with env.factory() as s:
        lead = start(s, env.tool)
        assert flow.dispatch_due(s, lead, env.tool, NOW, settings=S)
        s.commit()
        lead_id = lead.id
    clock.t = NOW + timedelta(days=1)
    worker.tick(env.factory, env.deps, clock, S)
    with env.factory() as s:
        assert _alerts(s, "stuck_lead") == 0

    clock.t = NOW + timedelta(days=2, hours=1)
    worker.tick(env.factory, env.deps, clock, S)
    worker.tick(env.factory, env.deps, clock, S)

    with env.factory() as s:
        texts = list(s.scalars(select(AlertRow.text).where(AlertRow.kind == "stuck_lead")))
    assert texts == [f"lead {lead_id}: email 1 was handed to the sender 2 days ago and is not sent; "
                     "check the sender"]


# ------------------------------------------------------------------ 6. schedule edits keep shifts; held leads too

def test_resume_shift_survives_schedule_save(session, tool):
    lead, _ = _after_email_1(session, tool)
    flow.pause(session, lead, tool, "pm", NOW)
    late = rows(session, lead)[1].due_at + timedelta(days=3)
    flow.resume(session, lead, tool, "pm", late, settings=S)
    shifted = [r.due_at for r in rows(session, lead)]

    sch.save_schedule(session, sch.get_schedule(session), "pm", late + HOUR, settings=S)

    assert [r.due_at for r in rows(session, lead)] == shifted


def test_paused_lead_removed_email_is_cancelled(session, tool):
    lead, _ = _after_email_1(session, tool)
    lead.state = LeadState.PAUSED.value

    sch.save_schedule(session, sch.get_schedule(session)[:3], "pm", NOW + HOUR, settings=S)

    assert (rows(session, lead)[3].status, rows(session, lead)[3].reason) == ("cancelled", "removed")


# ------------------------------------------------------------------ 7. account hold end shifts overdue emails

def test_account_hold_end_shifts_overdue_emails(session, tool):
    lead, _ = _after_email_1(session, tool)
    lead.state, lead.pause_reason = LeadState.PAUSED.value, "account"
    late = rows(session, lead)[1].due_at + timedelta(days=5)

    assert resume_account_holds(session, tool, late, settings=S) == 1

    assert lead.state == LeadState.IN_SEQUENCE.value and rows(session, lead)[1].due_at == late


# ------------------------------------------------------------------ 8. lead controls only for P0 leads in outreach

def test_controls_refuse_non_p0_or_finished_leads(session, tool):
    plain = make_lead(session, active_channel="email")
    with pytest.raises(P0Error):
        flow.pause(session, plain, tool, "pm", NOW)
    lead, deps = _after_email_1(session, tool)
    lead.state = LeadState.ENGAGED.value
    for call in (lambda: flow.pause(session, lead, tool, "pm", NOW),
                 lambda: flow.resume(session, lead, tool, "pm", NOW, settings=S),
                 lambda: flow.stop(session, lead, deps, "pm", NOW),
                 lambda: flow.send_now(session, lead, tool, "pm", NOW, settings=S)):
        with pytest.raises(P0Error):
            call()


def build(tmp_path):
    t = MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=lambda: NOW, smtp_factory=SMTPStub)
    engine = make_app_engine()
    settings = make_settings(app_env="local", email_flow="p0")
    app = create_app(settings, deps=make_deps(tool=t), engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    return TestClient(app, raise_server_exceptions=False), engine


def test_control_route_400_for_non_p0_lead(tmp_path):
    client, engine = build(tmp_path)
    with sessionmaker(engine)() as s:
        lead_id = make_lead(s, active_channel="email").id
        s.commit()

    r = client.post(f"/demo/api/p0/leads/{lead_id}/pause", json={"by": "pm"})

    assert r.status_code == 400 and "error" in r.json()


# ------------------------------------------------------------------ minor fixes

def test_finish_only_for_email_leads(env, clock):
    with env.factory() as s:
        lead = start(s, env.tool)
        for r in rows(s, lead):
            r.status, r.sent_at = "sent", NOW - timedelta(days=6)
        lead.active_channel = "whatsapp"
        s.commit()
        lead_id = lead.id
    ctx = worker.Ctx(session_factory=env.factory, deps=env.deps, settings=S, now=NOW, now_fn=clock, batch=50,
                     scanners=SCANNERS)

    with env.factory() as s:
        assert worker._p0_finish(ctx, s) == 0
        assert s.get(Lead, lead_id).state == LeadState.IN_SEQUENCE.value


def test_sender_uses_first_email_slot_for_every_p0_step(tool):
    tool.send_window = window_from_settings(make_settings())
    sat = datetime(2026, 11, 14, 8, 0, tzinfo=IST).astimezone(timezone.utc)
    v = {"plan": "1", "due_at_3": sat.isoformat()}

    classic = tool.planned_due(v, 3, key="k")
    p0_due = tool.planned_due({**v, "p0": "1"}, 3, key="k")

    assert p0_due == tool.send_window.slot(sat, 1, "k") and p0_due != classic


WINDOWED = make_settings(email_flow="p0", email_send_window="Mon-Fri 09:30-17:30")


def test_example_dates_use_send_slot(session, tool):
    set_schedule(session, [0, 1])
    sat = datetime(2026, 11, 14, 11, 0, tzinfo=IST).astimezone(timezone.utc)

    got = sch.example_dates(session, sat, settings=WINDOWED)

    assert [r["date"] for r in got] == ["2026-11-16", "2026-11-16"]


def test_example_dates_for_unsaved_days(session, tool):
    set_schedule(session, [0])

    got = sch.example_dates(session, NOW, settings=S, days=[0, 3])

    assert [(r["email_no"], r["date"]) for r in got] == [(1, "2026-11-12"), (2, "2026-11-16")]


def test_lead_view_shows_send_slot_date(session, tool):
    set_schedule(session, [0, 2])
    lead = start(session, tool)

    view = planner.lead_view(session, lead, NOW, settings=WINDOWED)

    assert [r["due_date"] for r in view] == ["2026-11-12", "2026-11-16"]  # day 2 is a Saturday


def test_pause_always_pauses_the_sender(session, tool):
    lead = start(session, tool)

    flow.pause(session, lead, tool, "pm", NOW)

    assert tool_status(tool, lead) == "paused"


def test_replan_logs_unreadable_day0(session, tool, caplog):
    lead = start(session, tool)
    lead.variant_flags = {**lead.variant_flags, "p0_day0": "garbage"}

    with caplog.at_level(logging.WARNING, logger="outreach.p0.planner"):
        sch.save_schedule(session, sch.get_schedule(session), "pm", NOW, settings=S)

    assert f"lead {lead.id}" in caplog.text


def test_routes_reject_non_int_template_ids(tmp_path):
    client, _ = build(tmp_path)
    client.get("/demo/api/p0/templates")

    bad = client.post("/demo/api/p0/templates", json={"id": "abc", "name": "x", "subject": "s", "body": "b",
                                                      "by": "pm"})
    pv = client.post("/demo/api/p0/preview", json={"template_id": "x"})
    ex = client.post("/demo/api/p0/schedule/example", json={"rows": [{"day": 0}, {"day": 4}]})

    assert bad.status_code == 400 and pv.status_code == 400
    assert ex.status_code == 200 and [r["email_no"] for r in ex.json()["example"]] == [1, 2]


def test_lead_panel_counts_p0_emails(tmp_path):
    client, engine = build(tmp_path)
    client.get("/demo/api/p0/schedule")
    with sessionmaker(engine)() as s:
        lead = make_lead(s, active_channel="email")
        planner.plan_lead(s, lead, NOW, settings=S)
        first = planner.next_row(s, lead)
        first.status, first.sent_at = "sent", NOW
        s.commit()
        lead_id = lead.id

    got = client.get(f"/demo/api/lead/{lead_id}").json()["lead"]

    assert (got["emails_sent"], got["emails_total"]) == (1, 4)
