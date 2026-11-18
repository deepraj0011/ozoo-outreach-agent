"""Task 19: our own sender sends inside working hours, follow-ups rotating afternoon / morning."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from outreach import mailpit_sender
from outreach.emailtool.mailpit import MailpitEmailTool
from outreach.hours import parse_business_hours
from outreach.sendwindow import MAX_OFFSET_MIN, STEP_TIMES, SendWindow, window_from_settings
from outreach.sequencer import SCHEDULE_DAYS
from tests.fakes import make_settings
from tests.test_mailpit import SMTPStub, _vars, lead
from tests.test_local_settings import local

IST = ZoneInfo("Asia/Kolkata")
WIN = SendWindow(parse_business_hours("Mon-Fri 09:30-17:30"), IST)


def ist(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=IST).astimezone(timezone.utc)


def local_time(dt):
    return dt.astimezone(IST)


# 2026-11-16 is a Monday
def test_email_1_inside_the_window_goes_now():
    due = ist(2026, 11, 16, 11, 0)
    assert WIN.slot(due, 1, "lead-a") == due


def test_email_1_at_night_waits_for_the_next_morning():
    out = local_time(WIN.slot(ist(2026, 11, 16, 22, 0), 1, "lead-a"))
    assert out.date().isoformat() == "2026-11-17"
    assert time(9, 30) <= out.time() <= time(10, 10)


def test_friday_evening_waits_for_monday():
    out = local_time(WIN.slot(ist(2026, 11, 20, 19, 0), 1, "lead-a"))
    assert out.weekday() == 0 and out.date().isoformat() == "2026-11-23"


def test_follow_ups_rotate_afternoon_morning_afternoon():
    due = ist(2026, 11, 16, 8, 0)
    got = {step: local_time(WIN.slot(due, step, "lead-a")).time() for step in (2, 3, 4)}
    for step, t in got.items():
        start = datetime.combine(datetime(2026, 11, 16), STEP_TIMES[step])
        assert start <= datetime.combine(datetime(2026, 11, 16), t) <= start + timedelta(minutes=MAX_OFFSET_MIN)
    assert got[2].hour >= 15 and got[3].hour == 10 and got[4].hour >= 15


def test_follow_up_due_after_its_slot_goes_the_next_working_day():
    out = local_time(WIN.slot(ist(2026, 11, 20, 16, 30), 2, "lead-a"))  # Friday 16:30, slot is 15:00
    assert out.date().isoformat() == "2026-11-23" and out.hour == 15


def test_offset_spreads_a_batch_but_is_stable_per_lead():
    due = ist(2026, 11, 16, 8, 0)
    times = {WIN.slot(due, 2, f"lead-{i}") for i in range(20)}
    assert len(times) > 5
    assert WIN.slot(due, 2, "lead-7") == WIN.slot(due, 2, "lead-7")


def test_slot_never_falls_outside_a_short_window():
    short = SendWindow(parse_business_hours("Mon-Fri 09:00-12:00"), IST)
    out = local_time(short.slot(ist(2026, 11, 16, 8, 0), 2, "lead-a"))  # wants 15:00
    assert time(9, 0) <= out.time() < time(12, 0)


def test_never_returns_before_due():
    for h in range(0, 24):
        due = ist(2026, 11, 18, h, 17)
        for step in (1, 2, 3, 4):
            assert WIN.slot(due, step, "k") >= due


def test_window_from_settings():
    assert window_from_settings(local()) is None  # local demo: never waits for office hours
    prod = window_from_settings(make_settings())
    assert prod is not None and prod.hours == parse_business_hours("Mon-Fri 09:30-17:30")
    assert window_from_settings(make_settings(email_send_window="")) is None
    custom = window_from_settings(local(email_send_window="Tue-Thu 10:00-16:00", email_send_tz="Europe/London"))
    assert custom.hours.days == frozenset({1, 2, 3}) and str(custom.zone) == "Europe/London"


@pytest.mark.parametrize("bad", [{"email_send_window": "always"}, {"email_send_tz": "Mars/Base"}])
def test_bad_window_settings_are_rejected(bad):
    with pytest.raises(ValueError):
        make_settings(**bad)


# ---------------------------------------------------------------- in the sender


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_sender_holds_a_night_time_email_until_the_morning(tmp_path):
    clock = Clock(ist(2026, 11, 16, 23, 0))
    tool = MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)
    tool.send_window = WIN
    tool.add_lead("c1", lead(), _vars())
    assert tool.send_due(SCHEDULE_DAYS) == []
    clock.t = ist(2026, 11, 17, 10, 15)
    assert [s["step"] for s in tool.send_due(SCHEDULE_DAYS)] == [1]


def test_sped_up_local_clock_ignores_the_window(tmp_path):
    clock = Clock(ist(2026, 11, 16, 23, 0))
    tool = MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)
    tool.send_window = WIN
    tool.add_lead("c1", lead(), _vars())
    assert [s["step"] for s in tool.send_due(SCHEDULE_DAYS, 1440.0)] == [1]


def test_make_tool_sets_the_window_for_our_own_mailboxes(tmp_path):
    settings = SimpleNamespace(email_mode="gmail", tool_webhook_secret="s", gmail_address="me@example.com",
                               gmail_app_password="abcd efgh ijkl mnop", gmail_from_name="Ozoo.ai",
                               mailpit_state_file=str(tmp_path / "g.db"), app_env="production",
                               email_send_window=None, email_send_tz=None, business_tz="Asia/Kolkata")
    tool = mailpit_sender.make_tool(settings, now=lambda: ist(2026, 11, 16, 11, 0))
    assert tool.send_window is not None
