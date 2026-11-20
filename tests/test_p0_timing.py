"""P0 send timing per email: Day N (working hours), Now (email 1 only) or a fixed date and time (exact)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect

from outreach.db import init_db, make_engine
from outreach.p0 import flow
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from outreach.p0.templates import P0Error

from .conftest import NOW
from .test_p0_flow import GOOD, S, clock, rows, start, tool, tool_vars  # noqa: F401

AT_LOCAL = "2026-11-12T18:00"  # IST, the same day as NOW (15:30 IST)
AT_UTC = datetime(2026, 11, 12, 12, 30, tzinfo=timezone.utc)


def _templates(session, n):
    return [p0.save_template(session, {**GOOD, "name": f"T{i}", "status": "live"}, "pm", NOW, settings=S).id
            for i in range(n)]


def _save(session, rows_):
    return sch.save_schedule(session, rows_, "pm", NOW, settings=S)


def test_old_database_gets_the_new_columns(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE p0_schedule_row (id INTEGER PRIMARY KEY, email_no INTEGER UNIQUE, "
                          "template_id INTEGER, day INTEGER)")
        c.exec_driver_sql("INSERT INTO p0_schedule_row (email_no, template_id, day) VALUES (1, 1, 0)")

    init_db(engine)
    init_db(engine)  # idempotent

    assert {"mode", "at"} <= {c["name"] for c in inspect(engine).get_columns("p0_schedule_row")}
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT mode FROM p0_schedule_row").scalar() == "day"
    engine.dispose()


@pytest.mark.parametrize("rows_, message", [
    ([{"mode": "day", "day": 0}, {"mode": "now"}], "only email 1 can go Now"),
    ([{"mode": "at", "at": "2026-11-10T10:00"}], "must be in the future"),
    ([{"mode": "day", "day": 3}, {"mode": "at", "at": "2026-11-13T10:00"}], "Email 2 goes before email 1"),
    ([{"mode": "soon"}], "choose Now, Day or Date and time"),
])
def test_bad_timings_are_refused(session, rows_, message):
    ids = _templates(session, len(rows_))
    with pytest.raises(P0Error, match=message):
        _save(session, [{**r, "template_id": t} for r, t in zip(rows_, ids)])


def test_now_and_date_time_are_exact_and_skip_the_one_a_day_rule(session, tool, clock):
    t1, t2, t3 = _templates(session, 3)
    saved = _save(session, [{"template_id": t1, "mode": "now"},
                            {"template_id": t2, "mode": "at", "at": AT_LOCAL},
                            {"template_id": t3, "mode": "day", "day": 5}])
    assert [(r["mode"], r["at"]) for r in saved] == [("now", None), ("at", AT_LOCAL), ("day", None)]
    lead = start(session, tool)

    planned = rows(session, lead)
    assert [(r.due_at, r.exact) for r in planned[:2]] == [(NOW, True), (AT_UTC, True)]
    assert planned[2].exact is False

    assert flow.dispatch_due(session, lead, tool, NOW, settings=S)
    assert tool_vars(tool, lead)["due_exact_1"] == "1"
    planned[0].status, planned[0].sent_at = "sent", NOW
    assert not flow.dispatch_due(session, lead, tool, AT_UTC - timedelta(minutes=1), settings=S)
    assert flow.dispatch_due(session, lead, tool, AT_UTC, settings=S)  # same day: still goes, exactly
    assert tool_vars(tool, lead)["due_exact_2"] == "1"


def test_lead_starting_after_the_moment_still_gets_it_late(session, tool):
    t1, t2 = _templates(session, 2)
    _save(session, [{"template_id": t1, "mode": "day", "day": 0},
                    {"template_id": t2, "mode": "at", "at": AT_LOCAL}])
    later = AT_UTC + timedelta(days=1)

    lead = start(session, tool, now=later)

    late = rows(session, lead)[1]
    assert late.status == "planned" and late.due_at == later and late.exact is False
