"""P0 planner: when an email is due, and which calendar day that is."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..send_plan import _scaled

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _zone(settings: Any) -> ZoneInfo:
    return ZoneInfo(getattr(settings, "business_tz", None) or "Asia/Kolkata")


def _scale(settings: Any) -> float:
    return float(getattr(settings, "time_scale", 1.0) or 1.0)


def due_at(day0: datetime, day: int, *, settings: Any) -> datetime:
    """Day 0 is the start itself; day N is midnight (business time) N calendar days later.
    With a fast test clock, N scaled days after the start."""
    if day == 0:
        return day0
    if _scale(settings) != 1:
        return day0 + _scaled(day, settings)
    zone = _zone(settings)
    date = day0.astimezone(zone).date() + timedelta(days=day)
    return datetime.combine(date, time.min, tzinfo=zone).astimezone(timezone.utc)


def local_day(at: datetime, *, settings: Any) -> str:
    """The calendar day `at` falls on (business time), or the scaled-day bucket on a fast clock."""
    scale = _scale(settings)
    if scale == 1:
        return at.astimezone(_zone(settings)).date().isoformat()
    return str(int((at - EPOCH) / (timedelta(days=1) / scale)))
