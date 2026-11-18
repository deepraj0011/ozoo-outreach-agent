"""When our own sender (EMAIL_MODE=mailbox / gmail) actually sends a due email. Pure functions.

Real leads do not read every email: most see one or two of the four. Two things raise the odds that
the one they see is ours, and that it is on top of the inbox when they look:

* only send on working days inside working hours in the lead's market (default Mon-Fri 09:30-17:30
  Asia/Kolkata), never at 2 a.m. or on a Sunday, where it is buried by Monday morning;
* rotate the time of day: email 1 goes out at the first slot, the follow-ups alternate afternoon /
  morning / afternoon, so someone who only clears email after lunch still meets one of them.
  A per-lead offset of up to 40 minutes keeps a batch from going out in the same second.

Instantly (EMAIL_MODE=real) has its own campaign schedule; this module is only for our sender.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Mapping, Optional
from zoneinfo import ZoneInfo

from .hours import BusinessHours, business_zone, parse_business_hours

DEFAULT_WINDOW = "Mon-Fri 09:30-17:30"
STEP_TIMES: Mapping[int, time] = {2: time(15, 0), 3: time(10, 30), 4: time(15, 30)}  # follow-ups
MAX_OFFSET_MIN = 40
MAX_DAYS_AHEAD = 14


@dataclass(frozen=True)
class SendWindow:
    hours: BusinessHours
    zone: ZoneInfo

    def slot(self, due: datetime, step: int, key: str = "") -> datetime:
        """The send time for an email that is due at `due` (aware): the first moment at or after
        `due` that is inside the window, at the step's time of day for follow-ups. UTC-aware result."""
        offset = timedelta(minutes=_offset_minutes(key, step))
        local_due = due.astimezone(self.zone)
        wanted = STEP_TIMES.get(step)
        for days in range(MAX_DAYS_AHEAD + 1):
            day = local_due.date() + timedelta(days=days)
            if day.weekday() not in self.hours.days:
                continue
            opens = datetime.combine(day, self.hours.open, tzinfo=self.zone)
            closes = datetime.combine(day, self.hours.close, tzinfo=self.zone)
            start = datetime.combine(day, wanted, tzinfo=self.zone) + offset if wanted else opens + offset
            start = min(max(start, opens), closes - timedelta(minutes=1))
            if wanted is None and days == 0 and opens <= local_due < closes:
                return due  # email 1, already inside the window: send now
            if start >= local_due:
                return start.astimezone(due.tzinfo)
        return due  # a window with no working day: never hold an email back forever


def _offset_minutes(key: str, step: int) -> int:
    digest = hashlib.sha256(f"{key}:{step}".encode()).digest()
    return digest[0] % (MAX_OFFSET_MIN + 1)


def window_from_settings(settings: object) -> Optional[SendWindow]:
    """EMAIL_SEND_WINDOW: unset = on in production mailbox / gmail mode, off locally (a local demo
    must not wait for office hours); "" = off; "Mon-Fri 09:30-17:30" style = on everywhere.
    The zone is EMAIL_SEND_TZ, else BUSINESS_TZ."""
    spec = getattr(settings, "email_send_window", None)
    if spec is None:
        if getattr(settings, "app_env", "production") != "production":
            return None
        spec = DEFAULT_WINDOW
    spec = spec.strip()
    if not spec:
        return None
    zone = getattr(settings, "email_send_tz", None) or getattr(settings, "business_tz", "Asia/Kolkata")
    return SendWindow(parse_business_hours(spec), business_zone(zone))
