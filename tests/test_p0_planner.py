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
