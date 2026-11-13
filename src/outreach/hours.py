"""Business hours (BUSINESS_TZ / BUSINESS_HOURS) for the WhatsApp human SLA. Pure functions.

Public holidays are ignored: a weekday holiday counts as a business day, so an SLA can fire on a day
nobody is in. Add a holiday calendar here if that becomes noise."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import FrozenSet
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SPEC = re.compile(r"^\s*([A-Za-z]{3})(?:\s*-\s*([A-Za-z]{3}))?\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")
MAX_DAYS_AHEAD = 14  # a deadline search never runs longer than this


@dataclass(frozen=True)
class BusinessHours:
    days: FrozenSet[int]  # datetime.weekday(): Monday = 0
    open: time
    close: time


def parse_business_hours(spec: str) -> BusinessHours:
    """"Mon-Fri 09:00-18:00" (a day range or one day, then opening-closing time)."""
    m = _SPEC.match(spec or "")
    if m is None:
        raise ValueError("business hours must look like 'Mon-Fri 09:00-18:00'")
    first, last = m.group(1).lower(), (m.group(2) or m.group(1)).lower()
    if first not in DAYS or last not in DAYS:
        raise ValueError("unknown day in business hours")
    a, b = DAYS.index(first), DAYS.index(last)
    days = frozenset(range(a, b + 1)) if a <= b else frozenset(list(range(a, 7)) + list(range(0, b + 1)))
    try:
        opening = time(int(m.group(3)), int(m.group(4)))
        closing = time(int(m.group(5)), int(m.group(6)))
    except ValueError as exc:
        raise ValueError("bad time in business hours") from exc
    if opening >= closing:
        raise ValueError("business hours must open before they close")
    return BusinessHours(days, opening, closing)


def business_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown business timezone") from exc


def business_deadline(start: datetime, delta: timedelta, hours: BusinessHours, tz: ZoneInfo) -> datetime:
    """`start` plus `delta` of business time (UTC result). Time outside business hours does not count:
    a 1 h SLA starting Friday 17:30 with Mon-Fri 09:00-18:00 ends Monday 09:30."""
    t = start.astimezone(tz)
    remaining = delta
    for _ in range(MAX_DAYS_AHEAD * 2):
        day = t.date()
        opens = datetime.combine(day, hours.open, tzinfo=tz)
        closes = datetime.combine(day, hours.close, tzinfo=tz)
        if t.weekday() not in hours.days or t >= closes:
            t = datetime.combine(day + timedelta(days=1), hours.open, tzinfo=tz)
            continue
        if t < opens:
            t = opens
        available = closes - t
        if remaining <= available:
            return (t + remaining).astimezone(timezone.utc)
        remaining -= available
        t = datetime.combine(day + timedelta(days=1), hours.open, tzinfo=tz)
    raise ValueError("no business hours found")  # pragma: no cover - parse guarantees a day
