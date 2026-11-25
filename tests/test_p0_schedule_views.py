"""P0 schedule views (task 32): up to 15 emails, and the dates shown are the dates the sender really uses."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from outreach.emailtool.mailpit import MAX_STEPS
from outreach.p0 import flow, planner
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from outreach.p0.board import board

from .conftest import NOW
from .fakes import make_deps, make_settings
from .test_p0_flow import S, SMTPStub, _reset_smtp, clock, event, rows, start, tool  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")
GOOD = {"subject": "Hi {first_name}", "body": "Hello {first_name}"}
WINDOWED = make_settings(email_flow="p0", email_send_window="Mon-Fri 09:30-17:30")


def _templates(session, n):
    return [p0.save_template(session, {**GOOD, "name": f"T{i}", "status": "live"}, "pm", NOW, settings=S)
            for i in range(n)]


def _rows(tpls, days):
    return [{"template_id": t.id, "day": d} for t, d in zip(tpls, days)]


# ------------------------------------------------------------------ 15 emails

def test_fifteen_email_schedule_saves_and_plans_fifteen_rows(session, tool):
    tpls = _templates(session, 15)

    saved = sch.save_schedule(session, _rows(tpls, range(15)), "pm", NOW, settings=S)
    lead = start(session, tool)

    assert len(saved) == 15 == sch.MAX_EMAILS
    assert [r.email_no for r in rows(session, lead)] == list(range(1, 16))


def test_sixteen_emails_are_refused(session):
    tpls = _templates(session, 16)

    with pytest.raises(p0.P0Error, match="1 to 15"):
        sch.save_schedule(session, _rows(tpls, range(16)), "pm", NOW, settings=S)


def test_sender_sends_all_fifteen_emails(session, tool, clock):
    assert MAX_STEPS >= 15
    tpls = _templates(session, 15)
    sch.save_schedule(session, _rows(tpls, range(15)), "pm", NOW, settings=S)
    lead = start(session, tool)
    deps = make_deps(tool=tool)

    for k in range(15):
        at = NOW + timedelta(days=k)
        clock.t = at
        assert flow.dispatch_due(session, lead, tool, at, settings=S), f"email {k + 1} not handed"
        sent = tool.send_due({})
        assert [s["step"] for s in sent] == [k + 1]
        event(session, lead, "sent", at, deps, step=k + 1)

    assert [r.status for r in rows(session, lead)] == ["sent"] * 15


# ------------------------------------------------------------------ weekend dates

def _sat_handed_lead(session, tool, clock):
    """A lead whose email 2 (day 2) was handed to the sender on Saturday 14 Nov (no working hours)."""
    tpls = _templates(session, 2)
    sch.save_schedule(session, _rows(tpls, [0, 2]), "pm", NOW, settings=WINDOWED)
    lead = start(session, tool, settings=WINDOWED)
    deps = make_deps(tool=tool)
    assert flow.dispatch_due(session, lead, tool, NOW, settings=WINDOWED)
    event(session, lead, "sent", NOW, deps, step=1)
    planner.mark_sent(session, lead, 1, NOW)
    sat = datetime(2026, 11, 14, 0, 5, tzinfo=IST).astimezone(timezone.utc)
    assert flow.dispatch_due(session, lead, tool, sat, settings=WINDOWED)
    return lead, sat


def test_board_shows_handed_weekend_email_on_monday(session, tool, clock):
    lead, sat = _sat_handed_lead(session, tool, clock)

    got = board(session, sat, settings=WINDOWED)

    cell = next(c for c in got["leads"][0]["emails"] if c["email_no"] == 2)
    when = datetime.fromisoformat(cell["date"]).astimezone(IST)
    assert when.date().isoformat() == "2026-11-16" and when.hour >= 9  # Monday, in working hours
    assert cell["status"] == "handed" and cell["late"] is False


def test_handed_weekend_email_is_not_late_on_sunday(session, tool, clock):
    lead, sat = _sat_handed_lead(session, tool, clock)
    sunday_noon = sat + timedelta(days=1, hours=12)

    view = planner.lead_view(session, lead, sunday_noon, settings=WINDOWED)

    assert view[1]["due_date"] == "2026-11-16" and view[1]["late"] is False


def test_lead_view_dates_a_handed_email_by_its_hand_off(session, tool, clock):
    lead, sat = _sat_handed_lead(session, tool, clock)
    row = rows(session, lead)[1]
    row.due_at = sat - timedelta(days=7)  # e.g. paused for a week, then handed: the sender counts from hand-off

    view = planner.lead_view(session, lead, sat, settings=WINDOWED)

    assert view[1]["due_date"] == "2026-11-16"
