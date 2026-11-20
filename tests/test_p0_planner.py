"""P0 scheduled emails, task 2: due dates and calendar days."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from outreach.p0 import planner
from tests.conftest import NOW
from tests.fakes import make_settings

S = make_settings()
IST = ZoneInfo("Asia/Kolkata")


def test_day0_is_start_and_day_n_is_calendar_day():
    assert planner.due_at(NOW, 0, settings=S) == NOW
    assert planner.due_at(NOW, 3, settings=S).astimezone(IST) == datetime(2026, 11, 15, tzinfo=IST)


def test_late_evening_day1_is_next_calendar_day():
    day0 = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)

    assert planner.local_day(planner.due_at(day0, 1, settings=S), settings=S) == "2026-11-14"


def test_scaled_days():
    fast = make_settings(app_env="local", time_scale=288)

    assert planner.due_at(NOW, 1, settings=fast) == NOW + timedelta(minutes=5)


# ---- task 3: planned rows ----
import pytest
from sqlalchemy import select

from outreach.models import Event, PlannedEmail
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from tests.conftest import make_lead

GOOD = {"name": "Intro", "subject": "Hi {first_name}", "body": "Hello {first_name}"}


def _tpl(session, name):
    return p0.save_template(session, {**GOOD, "name": name, "status": "live"}, "pm", NOW, settings=S)


def _setup(session, days=(0, 3, 6, 9)):
    tpls = [_tpl(session, f"T{i}") for i in range(len(days))]
    sch.save_schedule(session, [{"template_id": t.id, "day": d} for t, d in zip(tpls, days)], "pm", NOW, settings=S)
    return tpls


def _rows(session, lead):
    return {r.email_no: r for r in session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead.id))}


def test_plan_lead_once(session):
    _setup(session)
    lead = make_lead(session)

    planner.plan_lead(session, lead, NOW, settings=S)
    planner.plan_lead(session, lead, NOW, settings=S)

    assert len(_rows(session, lead)) == 4
    assert lead.variant_flags["p0"] is True


def test_replan_rewrites_planned_by_number(session):
    tpls = _setup(session)
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)
    rows = _rows(session, lead)
    rows[1].status = "sent"
    old = local = planner.local_day(rows[2].due_at, settings=S)
    sch.save_schedule(session, [{"template_id": t.id, "day": d} for t, d in zip(tpls, (0, 4, 6, 9))],
                      "pm", NOW, settings=S)
    sch.save_schedule(session, [{"template_id": t.id, "day": d} for t, d in zip(tpls, (0, 6, 7, 9))],
                      "pm", NOW, settings=S)

    rows = _rows(session, lead)
    assert planner.local_day(rows[2].due_at, settings=S) == "2026-11-18"
    assert rows[1].status == "sent" and rows[1].due_at == NOW


def test_replan_leaves_handed_row(session):
    tpls = _setup(session)
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)
    _rows(session, lead)[2].status = "handed"
    sch.save_schedule(session, [{"template_id": tpls[0].id, "day": 0}, {"template_id": tpls[3].id, "day": 3},
                                {"template_id": tpls[2].id, "day": 6}], "pm", NOW, settings=S)

    rows = _rows(session, lead)
    assert rows[2].status == "handed" and rows[2].template_id == tpls[1].id
    assert rows[4].status == "cancelled" and rows[4].reason == "removed"


def test_replan_skips_template_already_sent(session):
    tpls = _setup(session, (0, 3, 6))
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)
    _rows(session, lead)[1].status = "sent"
    sch.save_schedule(session, [{"template_id": tpls[0].id, "day": 0}, {"template_id": tpls[1].id, "day": 3},
                                {"template_id": tpls[0].id, "day": 6}], "pm", NOW, settings=S)

    row = _rows(session, lead)[3]
    assert (row.status, row.reason) == ("skipped", "already_sent")


def test_shift_overdue_keeps_gaps(session):
    _setup(session, (0, 3))
    lead = make_lead(session)
    rows = planner.plan_lead(session, lead, NOW, settings=S)
    rows[0].due_at, rows[1].due_at = NOW - timedelta(days=3), NOW

    planner.shift_overdue(rows, NOW, settings=S)

    assert rows[0].due_at == NOW and rows[1].due_at == NOW + timedelta(days=3)


def test_cancel_remaining(session):
    _setup(session, (0, 3, 6))
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)
    rows = _rows(session, lead)
    rows[1].status, rows[2].status = "sent", "handed"

    n = planner.cancel_remaining(session, lead, "replied", NOW)

    rows = _rows(session, lead)
    assert n == 2 and rows[1].status == "sent"
    assert rows[2].status == rows[3].status == "cancelled"


def test_mark_sent_flips_handed_row(session):
    _setup(session, (0, 3))
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)
    _rows(session, lead)[2].status = "handed"
    _rows(session, lead)[2].tool_step = 2

    session.flush()
    row = planner.mark_sent(session, lead, 2, NOW)

    assert row.status == "sent" and row.sent_at == NOW


def test_lead_view_late_and_counts(session):
    _setup(session, (0, 3))
    lead = make_lead(session)
    rows = planner.plan_lead(session, lead, NOW - timedelta(days=2), settings=S)
    rows[0].due_at = NOW - timedelta(days=1)
    for verdict in ("human", "bot"):
        session.add(Event(lead_id=lead.id, channel="email", kind="click", occurred_at=NOW, received_at=NOW,
                          step=1, verdict=verdict))
    session.flush()

    view = planner.lead_view(session, lead, NOW, settings=S)

    assert view[0]["late"] is True and view[0]["clicks"] == 1 and view[1]["clicks"] == 0


def test_cannot_switch_off_template_on_planned_rows(session):
    tpls = _setup(session, (0,))
    extra = _tpl(session, "Extra")
    lead = make_lead(session)
    session.add(PlannedEmail(lead_id=lead.id, email_no=1, template_id=extra.id, due_at=NOW, status="planned"))
    session.flush()

    with pytest.raises(p0.P0Error):
        p0.set_status(session, extra.id, "off", "pm", NOW)


def test_save_schedule_replans_current_leads(session):
    tpls = _setup(session, (0, 3))
    lead = make_lead(session)
    planner.plan_lead(session, lead, NOW, settings=S)

    sch.save_schedule(session, [{"template_id": tpls[0].id, "day": 0}, {"template_id": tpls[1].id, "day": 5}],
                      "pm", NOW, settings=S)

    assert planner.local_day(_rows(session, lead)[2].due_at, settings=S) == "2026-11-17"
