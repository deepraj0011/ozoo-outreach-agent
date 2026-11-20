"""P0 scheduled emails, task 9: the whole journey from the PM's schedule to the sent emails."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from outreach.p0 import flow
from outreach.p0 import schedule as sch
from outreach.p0 import templates as p0
from outreach.states import LeadState

from .conftest import NOW
from .fakes import make_deps
from .test_p0_flow import S, SMTPStub, _reset_smtp, clock, event, rows, start, tool, tool_status  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")
HOUR = timedelta(hours=1)


def _run_clock(session, lead, tool, clock, deps, hours, *, stop_after=None, on_hour=None):
    """Move the clock hour by hour: hand over what is due, let the sender send, feed the sent events back."""
    sent_at = []
    for h in range(hours):
        clock.t = NOW + h * HOUR
        if on_hour:
            on_hour(h)
        flow.dispatch_due(session, lead, tool, clock.t, settings=S)
        for item in tool.send_due({}):
            event(session, lead, "sent", clock.t, deps, step=item["step"])
            sent_at.append(clock.t)
        if stop_after and len(sent_at) >= stop_after and lead.state != LeadState.IN_SEQUENCE.value:
            break
    return sent_at


def _schedule(session, days):
    tpls = [p0.save_template(session, {"name": f"T{i + 1}", "subject": f"Subject number {i + 1}",
                                       "body": f"Body marker {i + 1} for {{first_name}}", "status": "live"},
                             "pm", NOW, settings=S) for i in range(len(days))]
    sch.save_schedule(session, [{"template_id": t.id, "day": d} for t, d in zip(tpls, days)], "pm", NOW,
                      settings=S)


def _local_day(t):
    return t.astimezone(IST).date()


def test_p0_full_journey(session, tool, clock):
    _schedule(session, [0, 2, 5])
    lead = start(session, tool)
    deps = make_deps(tool=tool)

    def click_midway(h):
        if h == 30:
            event(session, lead, "click", clock.t, deps, tag="pricing", step=1)

    sent_at = _run_clock(session, lead, tool, clock, deps, 24 * 9, on_hour=click_midway)

    assert len(sent_at) == 3 and len({_local_day(t) for t in sent_at}) == 3
    assert [r.status for r in rows(session, lead)] == ["sent"] * 3
    subjects = [m["Subject"] for m in SMTPStub.sent]
    assert subjects[0] == "Subject number 1"
    assert subjects[1:] == [f"Re: {subjects[0]}"] * 2
    bodies = [m.get_body(preferencelist=("plain",)).get_content() for m in SMTPStub.sent]
    assert [f"Body marker {i}" in b for i, b in enumerate(bodies, 1)] == [True] * 3
    assert lead.variant_flags["p0_clicks"] == 1
    assert lead.state == LeadState.IN_SEQUENCE.value


def test_p0_journey_unsubscribe_mid_way(session, tool, clock):
    _schedule(session, [0, 2, 5])
    lead = start(session, tool)
    deps = make_deps(tool=tool)
    unsub_done = []

    def unsubscribe_after_two(h):
        if not unsub_done and lead.step_sent == 2:
            event(session, lead, "unsubscribe", clock.t, deps)
            unsub_done.append(h)

    _run_clock(session, lead, tool, clock, deps, 24 * 9, on_hour=unsubscribe_after_two)

    assert unsub_done and len(SMTPStub.sent) == 2
    assert [r.status for r in rows(session, lead)] == ["sent", "sent", "cancelled"]
    assert lead.state == LeadState.SUPPRESSED.value and tool_status(tool, lead) == "stopped"
