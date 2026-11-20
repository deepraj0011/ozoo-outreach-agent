"""P0 scheduled emails, tasks 5 and 7: leads start on the schedule, the worker hands each due email to our
own sender, and the per-lead controls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from outreach import worker
from outreach.actions import execute
from outreach.db import init_db, make_engine
from outreach.emailtool.mailpit import MailpitEmailTool
from outreach.models import Event, Lead, P0Template, PlannedEmail
from outreach.outreach import start_outreach
from outreach.p0 import flow, planner
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.states import LeadState

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_deps, make_settings

IST = ZoneInfo("Asia/Kolkata")
S = make_settings(email_flow="p0")
GOOD = {"subject": "Hi {first_name}", "body": "Hello {first_name}"}


class SMTPStub:
    sent: list = []

    def __init__(self, host, port):
        pass

    def send_message(self, msg):
        SMTPStub.sent.append(msg)

    def quit(self):
        pass


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture(autouse=True)
def _reset_smtp():
    SMTPStub.sent = []


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tool(tmp_path, clock):
    return MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)


def tool_vars(tool, lead):
    with tool._db() as db:
        return json.loads(tool._row(db, lead.tool_lead_id)["variables"])


def tool_status(tool, lead):
    with tool._db() as db:
        return tool._row(db, lead.tool_lead_id)["status"]


def rows(session, lead):
    session.flush()
    return list(session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead.id)
                                .order_by(PlannedEmail.email_no)))


def set_schedule(session, days):
    tpls = [p0.save_template(session, {**GOOD, "name": f"T{i}", "status": "live"}, "pm", NOW, settings=S)
            for i in range(len(days))]
    sch.save_schedule(session, [{"template_id": t.id, "day": d} for t, d in zip(tpls, days)], "pm", NOW,
                      settings=S)


def start(session, tool, now=NOW, settings=S, **kw):
    lead = make_lead(session, active_channel="email", verify_status="valid", **kw)
    deps = make_deps(tool=tool)
    assert start_outreach(session, lead, deps, now, settings=settings) == "started"
    session.flush()
    return lead


def event(session, lead, kind, at, deps, **kw):
    ev = Event(lead_id=lead.id, channel=kw.pop("channel", "email"), kind=kind, occurred_at=at, received_at=at,
               verdict=kw.pop("verdict", "human"), processed=True, data=kw.pop("data", {}), **kw)
    session.add(ev)
    session.flush()
    snap = LeadSnapshot.from_lead(lead, at, clicks_need_check=True)
    execute(session, lead, decide(snap, EventSnapshot.from_event(ev), at), deps, at, event=ev)
    session.flush()
    return ev


# ------------------------------------------------------------------ task 5

def test_new_lead_gets_rows_and_tool_waits(session, tool):
    lead = start(session, tool)

    assert [r.status for r in rows(session, lead)] == ["planned"] * 4
    v = tool_vars(tool, lead)
    assert v["plan"] == "1" and v["steps"] == "4" and "subject_1" not in v
    assert tool.send_due({}) == []


def test_dispatch_hands_email1_then_sent_marks_row(session, tool):
    lead = start(session, tool)

    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)

    r1 = rows(session, lead)[0]
    tpl = session.get(P0Template, r1.template_id)
    assert r1.status == "handed" and r1.tool_step == 1
    assert tool_vars(tool, lead)["subject_1"] == p0.render(lead, tpl, 1, settings=S)["subject"]
    assert r1.sent_subject == tool_vars(tool, lead)["subject_1"]
    sent = tool.send_due({})
    assert [s["step"] for s in sent] == [1]
    event(session, lead, "sent", NOW, make_deps(tool=tool), step=1)
    assert rows(session, lead)[0].status == "sent" and lead.step_sent == 1


def test_one_email_per_day_after_window_shift(session, tool):
    set_schedule(session, [0, 1])
    fri = datetime(2026, 11, 13, 23, 30, tzinfo=IST).astimezone(timezone.utc)
    lead = start(session, tool, now=fri)
    assert flow.dispatch_due(session, lead, tool, fri, settings=S)
    mon = datetime(2026, 11, 16, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    event(session, lead, "sent", mon, make_deps(tool=tool), step=1)

    assert not flow.dispatch_due(session, lead, tool, mon + timedelta(minutes=30), settings=S)
    tue = datetime(2026, 11, 17, 10, 0, tzinfo=IST).astimezone(timezone.utc)
    assert flow.dispatch_due(session, lead, tool, tue, settings=S)
    assert rows(session, lead)[1].status == "handed" and rows(session, lead)[1].tool_step == 2


def test_classic_flow_unchanged_when_setting_off(session, tool):
    lead = start(session, tool, settings=make_settings())

    assert rows(session, lead) == []
    v = tool_vars(tool, lead)
    assert all(f"subject_{k}" in v for k in (1, 2, 3, 4)) and "plan" not in v


def test_import_start_time_is_day0(session, tool):
    later = NOW + timedelta(days=2)
    lead = start(session, tool, variant_flags={"plan_start": later.isoformat()})

    assert rows(session, lead)[0].due_at == later
    assert not flow.dispatch_due(session, lead, tool, NOW, settings=S)


@pytest.fixture
def env(tmp_path, clock):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tool = MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)
    yield SimpleNamespace(factory=factory, tool=tool, deps=make_deps(tool=tool))
    engine.dispose()


def test_dispatch_is_idempotent(env, clock):
    with env.factory() as s:
        lead = make_lead(s, active_channel="email", verify_status="valid")
        s.commit()
        lead_id = lead.id
    worker.tick(env.factory, env.deps, clock, S)
    worker.tick(env.factory, env.deps, clock, S)
    with env.factory() as s:
        lead = s.get(Lead, lead_id)
        assert start_outreach(s, lead, env.deps, NOW, settings=S) == "already"
        statuses = [r.status for r in rows(s, lead)]
    assert statuses == ["handed", "planned", "planned", "planned"]
    assert [x["step"] for x in env.tool.send_due({})] == [1]


# ------------------------------------------------------------------ task 7

def _controls(session, lead):
    session.flush()
    return [e.data["action"] for e in session.scalars(select(Event).where(
        Event.lead_id == lead.id, Event.kind == "p0_control").order_by(Event.id))]


def test_pause_holds_and_resume_shifts(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    event(session, lead, "sent", NOW, make_deps(tool=tool), step=1)
    flow.pause(session, lead, tool, "pm", NOW)
    r2 = rows(session, lead)[1]
    late = r2.due_at + timedelta(days=3)

    assert not flow.dispatch_due(session, lead, tool, late, settings=S)
    flow.resume(session, lead, tool, "pm", late, settings=S)

    after = rows(session, lead)
    assert after[1].due_at == late and after[2].due_at - after[1].due_at > timedelta(0)
    assert flow.dispatch_due(session, lead, tool, late, settings=S)
    assert _controls(session, lead) == ["pause", "resume"]


def test_stop_cancels_and_nurtures(session, tool):
    lead = start(session, tool)
    deps = make_deps(tool=tool)

    flow.stop(session, lead, deps, "pm", NOW)

    assert [r.status for r in rows(session, lead)] == ["cancelled"] * 4
    assert rows(session, lead)[0].reason == "p0_stopped"
    assert lead.state == LeadState.NURTURE.value and tool_status(tool, lead) == "stopped"
    assert _controls(session, lead) == ["stop"]


def test_send_now_hands_next_even_same_day(session, tool):
    lead = start(session, tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    event(session, lead, "sent", NOW, make_deps(tool=tool), step=1)
    later = NOW + timedelta(hours=1)

    assert not flow.dispatch_due(session, lead, tool, later, settings=S)
    assert flow.send_now(session, lead, tool, "pm", later, settings=S)

    r2 = rows(session, lead)[1]
    assert r2.status == "handed" and r2.tool_step == 2
    assert tool_vars(tool, lead)["due_exact_2"] == "1"
    assert _controls(session, lead) == ["send_now"]
