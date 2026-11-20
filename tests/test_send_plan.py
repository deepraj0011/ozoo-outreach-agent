"""Tasks 26-27: the email schedule. The product manager sets when each email goes and what kind it is
(sooner, and maybe another kind, if the lead showed interest); the agent still writes each email."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from outreach import send_plan as sp
from outreach import worker
from outreach.db import init_db, make_engine
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.mailpit import MailpitEmailTool
from outreach.models import Event, Lead, SendPlan
from outreach.outreach import Outreach
from outreach.sequencer import PATH_FLAG, PLAN_FLAG, enroll
from outreach.whatsapp.fake import FakeWhatsApp

from .conftest import NOW, make_lead
from .fakes import WEBHOOK_SECRET, make_deps, make_settings

DAY = timedelta(days=1)


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


def schedule(days=(4, 5, 6), end=5, kinds=("standard", "auto", "auto", "auto"), fast=None, start=None):
    """fast: {email index: (days, kind)} for "sooner if interested"."""
    emails = [{"kind": kinds[0]}]
    for i, d in enumerate(days, start=1):
        e = {"kind": kinds[i], "days": d}
        if fast and i in fast:
            e["fast"] = {"days": fast[i][0], "kind": fast[i][1]}
        emails.append(e)
    return {"start": start or {"mode": "on_add"}, "emails": emails, "end_days": end}


def save(session, data=None):
    row = sp.save_plan(session, {"data": data or sp.starter_plan()}, NOW)
    session.flush()
    return row


def vars_of(tool, lead):
    with tool._db() as db:
        return json.loads(tool._row(db, lead.tool_lead_id)["variables"])


def sent(session, lead, at):
    """The tool reported the decided email as sent."""
    lead.step_sent = (lead.step_sent or 0) + 1
    lead.last_sent_at = at
    session.flush()


def click(session, lead, at, tag="case_study", kind="click", verdict="human"):
    session.add(Event(lead_id=lead.id, channel="email", kind=kind, tag=tag, occurred_at=at, received_at=at,
                      verdict=verdict, processed=True, data={}))
    session.flush()


def enrolled(session, tool, settings, **kw):
    lead = make_lead(session, active_channel="email", **kw)
    enroll(session, lead, tool, NOW, settings=settings)
    return lead


def plan_of(session, lead):
    return sp.plan_for_lead(session, lead).data


# ------------------------------------------------------------------ the schedule itself

def test_starter_schedule_is_valid_and_matches_the_fixed_days():
    plan = sp.clean_plan(sp.starter_plan())
    assert [e.get("days") for e in plan["emails"]] == [None, 4, 5, 6] and plan["end_days"] == 5
    assert plan["emails"][1]["fast"] == {"days": 2, "kind": "case_study"}


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p["emails"].clear(), "at least email 1"),
    (lambda p: p["emails"].append({"kind": "auto", "days": 3}), "At most 4"),
    (lambda p: p["emails"][1].update(days=61), "0 to 60"),
    (lambda p: p["emails"][1].update(days=1.5), "whole number"),
    (lambda p: p["emails"][1].update(days=None), "number of days"),
    (lambda p: p["emails"][2].update(kind="poem"), "kind of email"),
    (lambda p: p["emails"][1].update(fast={"days": 9, "kind": "auto"}), "can't be later"),
    (lambda p: p["start"].update(mode="at", at="tomorrow"), "date and time"),
])
def test_bad_schedules_are_refused_with_a_clear_message(mutate, message):
    plan = schedule()
    mutate(plan)
    with pytest.raises(sp.PlanError, match=message):
        sp.clean_plan(plan)


def test_there_is_one_schedule_and_saving_again_updates_it(session):
    a = save(session)
    b = save(session, schedule(days=(1,)))
    assert a.id == b.id and session.query(SendPlan).count() == 1 and len(b.data["emails"]) == 2


def test_a_plan_from_the_branching_screen_is_converted():
    old = {"start": {"mode": "on_add"}, "first": "e1", "emails": {
        "e1": {"kind": "standard", "branches": [{"if": "clicked", "tag": "any", "wait": {"days": 2}, "then": "c2"},
                                                {"if": "any", "wait": {"days": 4}, "then": "n2"}]},
        "c2": {"kind": "case_study", "branches": [{"if": "any", "wait": {"days": 4}, "then": "end"}]},
        "n2": {"kind": "auto", "branches": [{"if": "any", "wait": {"days": 7}, "then": "end"}]}}}
    assert sp.upgrade(old) == {"start": {"mode": "on_add"}, "end_days": 7, "emails": [
        {"kind": "standard"}, {"kind": "auto", "days": 4, "fast": {"days": 2, "kind": "case_study"}}]}


def test_an_old_saved_plan_is_upgraded_when_read(session):
    session.add(SendPlan(name="Main", is_default=True, created_at=NOW, updated_at=NOW, data={
        "start": {"mode": "on_add"}, "first": "e1",
        "emails": {"e1": {"kind": "standard", "branches": [{"if": "any", "wait": {"days": 3}, "then": "end"}]}}}))
    session.flush()
    assert sp.current(session).data["emails"] == [{"kind": "standard"}]


# ------------------------------------------------------------------ enrolment and email 1

def test_without_a_schedule_the_fixed_days_are_unchanged(session, tool):
    lead = enrolled(session, tool, make_settings())
    assert "plan" not in vars_of(tool, lead) and PLAN_FLAG not in lead.variant_flags


def test_a_tool_that_cannot_schedule_ignores_the_schedule(session):
    save(session)
    lead = enrolled(session, FakeEmailTool(webhook_secret=WEBHOOK_SECRET), make_settings())
    assert PLAN_FLAG not in lead.variant_flags


def test_nothing_is_sent_until_the_schedule_decides(session, tool, clock):
    save(session)
    lead = enrolled(session, tool, make_settings())
    assert vars_of(tool, lead)["plan"] == "1"
    clock.t = NOW + 30 * DAY
    assert tool.send_due({1: 0, 2: 4, 3: 9, 4: 15}) == []


def test_email_1_on_a_picked_date_goes_at_that_time(session, tool, clock):
    settings = make_settings()
    save(session, schedule(start={"mode": "at", "at": "2026-11-20T11:00"}))
    lead = enrolled(session, tool, settings)
    assert sp.commit_next(session, lead, tool, NOW, settings) == "sent_to_tool"
    due = datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc)  # 11:00 Asia/Kolkata
    assert vars_of(tool, lead)["due_at_1"] == due.isoformat()
    clock.t = due - timedelta(minutes=1)
    assert tool.send_due({}) == []
    clock.t = due
    assert [s["step"] for s in tool.send_due({})] == [1]


def test_email_1_n_days_after_the_lead_is_added(session, tool):
    settings = make_settings()
    save(session, schedule(start={"mode": "days", "days": 3}))
    lead = enrolled(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW, settings)
    assert lead.variant_flags[sp.SCHED_FLAG]["due"] == (NOW + 3 * DAY).isoformat()


def test_a_start_picked_at_import_wins(session, tool):
    settings = make_settings()
    save(session)
    lead = make_lead(session, active_channel="email", variant_flags={"plan_start": (NOW + 2 * DAY).isoformat()})
    enroll(session, lead, tool, NOW, settings=settings)
    sp.commit_next(session, lead, tool, NOW, settings)
    assert vars_of(tool, lead)["due_exact_1"] == "1"
    assert lead.variant_flags[sp.SCHED_FLAG]["due"] == (NOW + 2 * DAY).isoformat()


def test_a_repeated_decision_decides_nothing_twice(session, tool):
    settings = make_settings()
    save(session)
    lead = enrolled(session, tool, settings)
    assert sp.commit_next(session, lead, tool, NOW, settings) == "sent_to_tool"
    assert sp.commit_next(session, lead, tool, NOW, settings) == "waiting"
    assert lead.variant_flags[PATH_FLAG] == ["e1"]


# ------------------------------------------------------------------ the days, and sooner if interested

def _after_email_1(session, tool, settings, data=None):
    save(session, data)
    lead = enrolled(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW, settings)
    sent(session, lead, NOW)
    return lead


def test_without_interest_the_next_email_goes_on_its_day(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    assert sp.commit_next(session, lead, tool, NOW + 3 * DAY, settings) == "waiting"
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "sent_to_tool"
    assert lead.variant_flags[sp.SCHED_FLAG]["kind"] == "auto"


def test_interest_sends_it_sooner_and_as_the_chosen_kind(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    nxt = sp.next_email(session, lead, plan_of(session, lead), settings)
    assert not nxt["fast"] and [o["when"] for o in nxt["options"]] == ["normal", "interested"]
    click(session, lead, NOW + DAY)
    assert sp.commit_next(session, lead, tool, NOW + 2 * DAY, settings) == "sent_to_tool"
    assert lead.variant_flags[sp.SCHED_FLAG]["kind"] == "case_study"
    assert "showed interest" in lead.variant_flags[sp.LOG_FLAG][-1]["what"]
    assert vars_of(tool, lead)["subject_2"]


def test_a_late_click_sends_at_once(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    click(session, lead, NOW + 3 * DAY)
    assert sp.commit_next(session, lead, tool, NOW + 3 * DAY, settings) == "sent_to_tool"


@pytest.mark.parametrize("how", ["read_page", "hot"])
def test_a_page_read_or_a_hot_score_is_interest(session, tool, how):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    if how == "hot":
        lead.score = 20
    else:
        click(session, lead, NOW + DAY, kind="visit", tag="pricing")
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["fast"]


def test_scanner_clicks_unsubscribe_and_earlier_clicks_are_not_interest(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    click(session, lead, NOW + DAY, verdict="bot")
    click(session, lead, NOW - DAY)
    click(session, lead, NOW + DAY, tag="unsubscribe")
    assert not sp.next_email(session, lead, plan_of(session, lead), settings)["fast"]


def test_the_send_window_moves_a_days_wait_but_not_a_picked_time(tool):
    tool.send_window = SimpleNamespace(slot=lambda due, step, key: due + timedelta(hours=7))
    assert tool.planned_due({"due_at_1": NOW.isoformat()}, 1) == NOW + timedelta(hours=7)
    assert tool.planned_due({"due_at_1": NOW.isoformat(), "due_exact_1": "1"}, 1) == NOW
    assert tool.planned_due({"due_at_1": "hold"}, 1) is None
    assert tool.planned_due({}, 1) is None


def test_after_the_last_email_the_sequence_ends(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings, schedule(days=(1, 1, 1), end=2))
    for day in (1, 2, 3):
        assert sp.commit_next(session, lead, tool, NOW + day * DAY, settings) == "sent_to_tool"
        sent(session, lead, NOW + day * DAY)
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "waiting"
    assert sp.commit_next(session, lead, tool, NOW + 5 * DAY, settings) == "end"
    assert lead.variant_flags[sp.ENDED_FLAG]


def test_a_shorter_schedule_ends_sooner(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings, schedule(days=(), end=3))
    assert sp.commit_next(session, lead, tool, NOW + 3 * DAY, settings) == "end"


def test_a_schedule_shortened_mid_way_ends_the_lead_after_its_last_email(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings)
    sent(session, lead, NOW + 4 * DAY)
    save(session, schedule(days=(4,), end=2))
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["step"] == 0


# ------------------------------------------------------------------ the product manager's changes

def test_move_kind_and_send_now_change_only_the_next_email(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    sp.change_next(session, lead, tool, "move", NOW, settings, days=1)
    sp.change_next(session, lead, tool, "kind", NOW, settings, kind="booking_slots")
    nxt = sp.next_email(session, lead, plan_of(session, lead), settings)
    assert (nxt["due"], nxt["exact"], nxt["kind"], nxt["changed"]) == (NOW + DAY, True, "booking_slots", True)
    assert sp.commit_next(session, lead, tool, NOW + DAY, settings) == "sent_to_tool"
    assert sp.lead_view(session, lead, settings)["path"][1]["kind"] == "booking_slots"  # as sent
    assert sp.NEXT_FLAG not in lead.variant_flags  # used up: the email after follows the schedule
    sent(session, lead, NOW + DAY)
    sp.change_next(session, lead, tool, "send_now", NOW + DAY, settings)
    assert sp.commit_next(session, lead, tool, NOW + DAY, settings) == "sent_to_tool"


def test_pause_stops_a_decided_email_and_resume_gives_its_date_back(session, tool):
    settings = make_settings()
    save(session)
    lead = enrolled(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW, settings)
    sp.change_next(session, lead, tool, "hold", NOW, settings)
    assert vars_of(tool, lead)["due_at_1"] == "hold"
    assert tool.send_due({}) == []
    assert sp.commit_next(session, lead, tool, NOW, settings) == "waiting"
    sp.change_next(session, lead, tool, "resume", NOW, settings)
    assert vars_of(tool, lead)["due_at_1"] == NOW.isoformat()
    assert [s["step"] for s in tool.send_due({})] == [1]


def test_changing_a_decided_email_updates_the_tool(session, tool):
    settings = make_settings()
    save(session, schedule(start={"mode": "days", "days": 5}))
    lead = enrolled(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW, settings)
    sp.change_next(session, lead, tool, "send_now", NOW, settings)
    v = vars_of(tool, lead)
    assert (v["due_at_1"], v["due_exact_1"]) == (NOW.isoformat(), "1")
    sp.change_next(session, lead, tool, "clear", NOW, settings)
    assert vars_of(tool, lead)["due_at_1"] == (NOW + 5 * DAY).isoformat()


def test_stop_takes_back_a_decided_email_and_ends_the_sequence(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "sent_to_tool"
    sp.change_next(session, lead, tool, "end", NOW + 4 * DAY, settings)
    assert vars_of(tool, lead)["due_at_2"] == "hold"
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "end"


def test_a_moved_date_in_the_past_means_now(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    sp.change_next(session, lead, tool, "move", NOW, settings, at="2026-01-01T10:00")
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["due"] == NOW


def test_a_fixed_days_lead_can_switch_to_the_schedule_at_its_next_email(session, tool):
    settings = make_settings()
    lead = enrolled(session, tool, settings)
    sent(session, lead, NOW)
    with pytest.raises(sp.PlanError, match="Save the email schedule"):
        sp.change_next(session, lead, tool, "use_plan", NOW, settings)
    save(session)
    sp.change_next(session, lead, tool, "use_plan", NOW, settings)
    assert vars_of(tool, lead)["plan"] == "1"
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["step"] == 2


def test_the_lead_view_and_calendar_show_the_next_email(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    view = sp.lead_view(session, lead, settings)
    assert view["on_plan"] and view["next"]["step"] == 2 and [x["step"] for x in view["later"]] == [3, 4]
    items = sp.upcoming(session, settings)
    assert [(i["lead_id"], i["step"]) for i in items] == [(lead.id, 2)]
    assert sp.view(session)["following"] == 1


# ------------------------------------------------------------------ the worker, end to end

@pytest.fixture
def env(tmp_path, clock):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = make_settings()
    tool = MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)
    outreach = Outreach(settings)
    deps = make_deps(tool=tool, wa=FakeWhatsApp(settings), outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(factory=factory, settings=settings, tool=tool, deps=deps)
    engine.dispose()


def test_worker_follows_the_schedule_from_email_1_to_the_end(env, clock):
    with env.factory() as s:
        sp.save_plan(s, {"data": schedule(days=(1, 1, 1), end=1)}, NOW)
        lead = make_lead(s, active_channel="email", verify_status="valid")
        s.commit()
        lead_id = lead.id
    for day in range(6):
        clock.t = NOW + day * DAY
        worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
        for item in env.tool.send_due({}):
            with env.factory() as s:
                ld = s.get(Lead, lead_id)
                ld.step_sent, ld.last_sent_at = item["step"], clock.t
                s.commit()
    assert len(SMTPStub.sent) == 4
    with env.factory() as s:
        ld = s.get(Lead, lead_id)
        assert ld.state == "nurture" and ld.variant_flags[sp.ENDED_FLAG]
        assert ld.variant_flags[PATH_FLAG] == ["e1", "e2", "e3", "e4"]


# ------------------------------------------------------------------ web routes

@pytest.fixture
def web(tmp_path, clock):
    from fastapi.testclient import TestClient

    from outreach.app import create_app
    from tests.fakes import TestSession, make_app_engine

    engine = make_app_engine()
    tool = MailpitEmailTool(WEBHOOK_SECRET, state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)
    deps = make_deps(tool=tool)
    settings = make_settings(app_env="local")
    app = create_app(settings, deps=deps, engine=engine, clock=lambda: NOW)
    yield SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), tool=tool, engine=engine,
                          Session=TestSession, settings=settings)
    engine.dispose()


def test_routes_save_read_preview_and_refuse_bad_schedules(web):
    c = web.client
    j = c.get("/demo/api/plans").json()
    assert j["plan"] is None and j["supported"] and len(j["options"]["starter"]["emails"]) == 4
    bad = c.post("/demo/api/plans/save", json={"data": schedule(days=(61,))})
    assert bad.status_code == 400 and "0 to 60" in bad.json()["error"]
    assert c.post("/demo/api/plans/save", content=b"x=1",
                  headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 400
    assert c.post("/demo/api/plans/save", json={"data": sp.starter_plan()}).json()["ok"]
    assert c.get("/demo/api/plans").json()["plan"]["data"]["end_days"] == 5
    pv = c.post("/demo/api/plans/preview", json={"step": 2, "kind": "case_study", "role": "Head of Collections"}).json()
    assert pv["ok"] and pv["subject"] and "/r/" not in pv["text"]  # real page links, never tracked ones
    assert c.post("/demo/api/plans/preview", json={"step": 9, "kind": "auto"}).status_code == 400


def test_routes_lead_schedule_change_and_upcoming(web):
    c = web.client
    c.post("/demo/api/plans/save", json={"data": schedule()})
    with web.Session(bind=web.engine) as s:
        lead = make_lead(s, active_channel="email")
        enroll(s, lead, web.tool, NOW, settings=web.settings)
        sp.commit_next(s, lead, web.tool, NOW, web.settings)
        s.commit()
        lead_id = lead.id
    view = c.get(f"/demo/api/lead/{lead_id}/schedule").json()
    assert view["on_plan"] and view["pending"]["step"] == 1 and view["schedule_saved"]
    r = c.post(f"/demo/api/lead/{lead_id}/schedule", json={"action": "hold"}).json()
    assert r["ok"] and "paused" in r["what"]
    assert c.post(f"/demo/api/lead/{lead_id}/schedule", json={"action": "explode"}).status_code == 400
    up = c.get("/demo/api/schedule/upcoming").json()["items"]
    assert up[0]["lead_id"] == lead_id and up[0]["hold"] is True
    assert c.get("/demo/api/lead/999/schedule").status_code == 404


def test_import_start_becomes_an_intake_field(web):
    from outreach.demo_console import _import_plan

    assert _import_plan("2026-11-24T11:00", web.settings) == {"plan_start": "2026-11-24T05:30:00+00:00"}
    assert _import_plan(None, web.settings) == {}
    with pytest.raises(ValueError):
        _import_plan("soon", web.settings)


def test_intake_stores_the_start(session):
    from outreach.intake import intake_lead
    from tests.test_intake import FakeVerifier

    res = intake_lead(session, {"name": "A B", "company": "C", "role": "CTO", "source": "t", "email": "a@c.com",
                                "plan_start": "2026-11-24T05:30:00+00:00"}, FakeVerifier(), NOW)
    assert session.get(Lead, res.lead_id).variant_flags["plan_start"] == "2026-11-24T05:30:00+00:00"


# ------------------------------------------------------------------ task 28: tracking vs. the schedule

def test_back_from_out_of_office_waits_a_day(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    back = NOW + 10 * DAY  # email 2 was due on day 4; the lead was away until day 10
    lead.variant_flags = {**lead.variant_flags, sp.BACK_FLAG: back.isoformat()}
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["due"] == back + DAY
    assert sp.commit_next(session, lead, tool, back, settings) == "waiting"
    assert sp.commit_next(session, lead, tool, back + DAY, settings) == "sent_to_tool"


def test_an_earlier_out_of_office_does_not_delay_later_emails(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    lead.variant_flags = {**lead.variant_flags, sp.BACK_FLAG: (NOW - DAY).isoformat()}
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["due"] == NOW + 4 * DAY


def test_interest_before_a_queued_email_goes_switches_it(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "sent_to_tool"  # queued, normal kind
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "waiting"       # no interest yet
    click(session, lead, NOW + 4 * DAY + timedelta(hours=1))
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY + timedelta(hours=2), settings) == "sent_to_tool"
    assert lead.variant_flags[sp.SCHED_FLAG]["kind"] == "case_study"
    assert "before it went" in lead.variant_flags[sp.LOG_FLAG][-1]["what"]
    assert sp.commit_next(session, lead, tool, NOW + 5 * DAY, settings) == "waiting"  # switched once only
    assert sp.lead_view(session, lead, settings)["path"][1] == {"step": 2, "kind": "case_study", "sooner": True}


def test_a_queued_email_the_pm_changed_is_not_switched(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings)
    sp.change_next(session, lead, tool, "kind", NOW + 4 * DAY, settings, kind="product")
    click(session, lead, NOW + 4 * DAY)
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "waiting"
    assert lead.variant_flags[sp.SCHED_FLAG]["kind"] == "product"


def test_sooner_with_last_ends_the_sequence_after_that_email(session, tool):
    settings = make_settings()
    data = schedule(fast={1: (1, "booking_slots")}, end=3)
    data["emails"][1]["fast"]["last"] = True
    lead = _after_email_1(session, tool, settings, data)
    click(session, lead, NOW + DAY)
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["last"]
    assert sp.commit_next(session, lead, tool, NOW + DAY, settings) == "sent_to_tool"
    assert sp.lead_view(session, lead, settings)["later"] == []
    sent(session, lead, NOW + DAY)
    nxt = sp.next_email(session, lead, plan_of(session, lead), settings)
    assert nxt["step"] == 0 and nxt["due"] == NOW + 4 * DAY
    assert sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings) == "end"


def test_without_interest_last_does_not_apply(session, tool):
    settings = make_settings()
    data = schedule(fast={1: (1, "booking_slots")})
    data["emails"][1]["fast"]["last"] = True
    assert sp.clean_plan(data)["emails"][1]["fast"]["last"] is True
    lead = _after_email_1(session, tool, settings, data)
    sp.commit_next(session, lead, tool, NOW + 4 * DAY, settings)
    sent(session, lead, NOW + 4 * DAY)
    assert sp.next_email(session, lead, plan_of(session, lead), settings)["step"] == 3


def test_the_lead_view_shows_interest_since_the_last_email(session, tool):
    settings = make_settings()
    lead = _after_email_1(session, tool, settings)
    assert not sp.lead_view(session, lead, settings)["interest_now"]
    click(session, lead, NOW + DAY)
    assert sp.lead_view(session, lead, settings)["interest_now"]


def test_worker_out_of_office_resume_marks_the_lead_back(env, clock):
    with env.factory() as s:
        sp.save_plan(s, {"data": schedule()}, NOW)
        lead = make_lead(s, active_channel="email", verify_status="valid")
        s.commit()
        lead_id = lead.id
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    with env.factory() as s:
        ld = s.get(Lead, lead_id)
        ld.state, ld.pause_reason, ld.pause_until = "paused", "ooo", NOW + 5 * DAY
        s.commit()
    clock.t = NOW + 5 * DAY
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    with env.factory() as s:
        ld = s.get(Lead, lead_id)
        assert ld.state == "in_sequence" and ld.variant_flags[sp.BACK_FLAG] == clock.t.isoformat()


def test_not_now_lead_gets_one_check_in_on_its_date(env, clock):
    with env.factory() as s:
        lead = make_lead(s, active_channel="email", verify_status="valid")
        s.commit()
        lead_id = lead.id
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    with env.factory() as s:
        ld = s.get(Lead, lead_id)
        ld.state, ld.nurture_until = "nurture", NOW + 30 * DAY
        s.commit()
    before = len(SMTPStub.sent)
    clock.t = NOW + 29 * DAY
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    assert len(SMTPStub.sent) == before
    clock.t = NOW + 30 * DAY
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    worker.tick(env.factory, env.deps, lambda: clock.t, env.settings)
    assert len(SMTPStub.sent) == before + 1
    assert "check back" in SMTPStub.sent[-1].get_body(("plain",)).get_content()
    with env.factory() as s:
        assert s.get(Lead, lead_id).variant_flags[worker.NURTURE_CHECKIN]
