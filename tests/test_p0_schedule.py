"""P0 scheduled emails, task 2: the schedule."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from outreach.models import Event, P0ScheduleRow
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from tests.conftest import NOW
from tests.fakes import make_settings

S = make_settings()
GOOD = {"name": "Intro", "subject": "Hi {first_name}", "body": "Hello {first_name}"}


def _tpl(session, name="Intro", status="live"):
    return p0.save_template(session, {**GOOD, "name": name, "status": status}, "pm", NOW, settings=S)


def _save(session, rows):
    return sch.save_schedule(session, rows, "pm", NOW, settings=S)


def test_save_and_read_back(session):
    a, b = _tpl(session, "A"), _tpl(session, "B")

    _save(session, [{"template_id": a.id, "day": 0}, {"template_id": b.id, "day": 3}])

    got = sch.get_schedule(session)
    assert [(r["email_no"], r["template_id"], r["template_name"], r["day"]) for r in got] == [
        (1, a.id, "A", 0), (2, b.id, "B", 3)]


def test_save_replaces_previous_rows_and_logs(session):
    a = _tpl(session)
    _save(session, [{"template_id": a.id, "day": 0}, {"template_id": a.id, "day": 2}])

    _save(session, [{"template_id": a.id, "day": 1}])

    assert len(session.scalars(select(P0ScheduleRow)).all()) == 1
    ev = session.scalars(select(Event).where(Event.kind == "p0_schedule")).all()
    assert len(ev) == 2 and ev[-1].data["by"] == "pm"


def test_days_must_increase(session):
    a = _tpl(session)
    with pytest.raises(p0.P0Error, match="later day"):
        _save(session, [{"template_id": a.id, "day": d} for d in (0, 3, 3)])


@pytest.mark.parametrize("count", [0, 11])
def test_one_to_ten_emails(session, count):
    a = _tpl(session)
    with pytest.raises(p0.P0Error):
        _save(session, [{"template_id": a.id, "day": i} for i in range(count)])


def test_template_must_be_live(session):
    draft = _tpl(session, status="draft")
    with pytest.raises(p0.P0Error, match="live"):
        _save(session, [{"template_id": draft.id, "day": 0}])


def test_bad_days_and_unknown_template(session):
    a = _tpl(session)
    with pytest.raises(p0.P0Error):
        _save(session, [{"template_id": a.id, "day": -1}])
    with pytest.raises(p0.P0Error):
        _save(session, [{"template_id": a.id, "day": "x"}])
    with pytest.raises(p0.P0Error):
        _save(session, [{"template_id": 9999, "day": 0}])


def test_failed_save_keeps_old_schedule(session):
    a = _tpl(session)
    _save(session, [{"template_id": a.id, "day": 0}])
    with pytest.raises(p0.P0Error):
        _save(session, [{"template_id": a.id, "day": 0}, {"template_id": a.id, "day": 0}])

    assert len(sch.get_schedule(session)) == 1


def test_example_dates_for_today(session):
    a = _tpl(session)
    _save(session, [{"template_id": a.id, "day": 0}, {"template_id": a.id, "day": 4}])

    got = sch.example_dates(session, NOW, settings=S)

    assert [(r["email_no"], r["day"], r["date"]) for r in got] == [(1, 0, "2026-11-12"), (2, 4, "2026-11-16")]
