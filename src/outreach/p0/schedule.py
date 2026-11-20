"""P0 schedule: which template goes as which email, on which day after the first."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Event, P0ScheduleRow, P0Template
from . import planner
from .templates import P0Error, _who

logger = logging.getLogger(__name__)

MAX_EMAILS = 10


def _local(at: Optional[datetime], settings: Any) -> Optional[str]:
    """A UTC moment as the screen's "YYYY-MM-DDTHH:MM" in business time."""
    return at.astimezone(planner._zone(settings)).strftime("%Y-%m-%dT%H:%M") if at else None


def parse_local(raw: Any, settings: Any) -> Optional[datetime]:
    """"YYYY-MM-DDTHH:MM" in business time -> UTC (None if not a date and time)."""
    try:
        at = datetime.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=planner._zone(settings))
    return at.astimezone(timezone.utc)


def get_schedule(session: Session, *, settings: Any = None) -> List[Dict[str, Any]]:
    rows = session.execute(
        select(P0ScheduleRow, P0Template.name).join(P0Template, P0Template.id == P0ScheduleRow.template_id)
        .order_by(P0ScheduleRow.email_no)).all()
    return [{"email_no": r.email_no, "template_id": r.template_id, "template_name": name, "day": r.day,
             "mode": r.mode or "day", "at": _local(r.at, settings)} for r, name in rows]


def _goes(now: datetime, row: Dict[str, Any], settings: Any) -> datetime:
    """When this email would go for a lead starting `now`."""
    due, exact = planner.row_due(now, row["mode"], row["day"], row["at"], settings=settings)
    return planner.goes_at(due, exact, settings=settings)


def _clean_row(session: Session, n: int, row: Dict[str, Any], now: datetime, settings: Any,
               kept_ats: List[datetime]) -> Dict[str, Any]:
    mode = str(row.get("mode") or "day")
    if mode not in planner.MODES:
        raise P0Error(f"Email {n}: choose Now, Day or Date and time.")
    if mode == "now" and n != 1:
        raise P0Error(f"Email {n}: only email 1 can go Now.")
    try:
        tid = int(row["template_id"])
    except (KeyError, TypeError, ValueError):
        raise P0Error(f"Email {n}: choose a template.") from None
    tpl = session.get(P0Template, tid)
    if tpl is None:
        raise P0Error(f"Email {n}: that template does not exist.")
    if tpl.status != "live":
        raise P0Error(f"Email {n}: the template must be live (it is {tpl.status}).")
    day, at = 0, None
    if mode == "day":
        try:
            day = int(row["day"])
        except (KeyError, TypeError, ValueError):
            raise P0Error(f"Email {n}: choose a day (a whole number).") from None
        if day < 0:
            raise P0Error(f"Email {n}: the day cannot be negative.")
    elif mode == "at":
        at = parse_local(row.get("at"), settings)
        if at is None:
            raise P0Error(f"Email {n}: pick a date and time.")
        if at <= now and at not in kept_ats:  # an unchanged saved moment may have passed meanwhile
            raise P0Error(f"Email {n}: the date and time must be in the future.")
    return {"template_id": tid, "mode": mode, "day": day, "at": at}


def _validate(session: Session, rows: List[Dict[str, Any]], now: datetime, settings: Any) -> List[Dict[str, Any]]:
    if not 1 <= len(rows) <= MAX_EMAILS:
        raise P0Error(f"The schedule needs 1 to {MAX_EMAILS} emails.")
    kept_ats = [r.at for r in session.scalars(select(P0ScheduleRow)) if r.at is not None]
    clean: List[Dict[str, Any]] = []
    for n, row in enumerate(rows, 1):
        cur = _clean_row(session, n, row, now, settings, kept_ats)
        if clean:
            prev = clean[-1]
            if cur["mode"] == prev["mode"] == "day" and cur["day"] <= prev["day"]:
                raise P0Error(f"Email {n} must go on a later day than email {n - 1}.")
            if _goes(now, cur, settings) < _goes(now, prev, settings):
                raise P0Error(f"Email {n} goes before email {n - 1}.")
        clean.append(cur)
    return clean


def save_schedule(session: Session, rows: List[Dict[str, Any]], by: str, now: datetime, *,
                  settings: Any) -> List[Dict[str, Any]]:
    """Replace the whole schedule (email_no = position + 1). Nothing changes if any row is invalid."""
    who = _who(by)
    clean = _validate(session, rows, now, settings)
    session.execute(delete(P0ScheduleRow))
    session.add_all([P0ScheduleRow(email_no=i, **r) for i, r in enumerate(clean, 1)])
    session.flush()
    planner.replan_all(session, now, settings=settings)
    session.add(Event(channel="system", kind="p0_schedule", occurred_at=now, received_at=now,
                      data={"emails": len(clean), "days": [r["day"] for r in clean],
                            "modes": [r["mode"] for r in clean], "by": who}))
    session.flush()
    logger.info("p0 schedule saved with %d emails", len(clean))
    return get_schedule(session, settings=settings)


def example_dates(session: Session, now: datetime, *, settings: Any, days: Optional[List[Any]] = None,
                  rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """When each email would go if a lead started `now`: `date` (business-time calendar day) and `when`
    (the moment; exact rows exactly, others in the send window). `rows`: unsaved rows from the screen
    ({mode, day, at}); `days`: unsaved day numbers; neither: the saved schedule. Unreadable rows get ""."""
    if rows is None:
        rows = ([{"mode": "day", "day": d} for d in days] if days is not None
                else get_schedule(session, settings=settings))
    out: List[Dict[str, Any]] = []
    for n, raw in enumerate(rows, 1):
        mode = str(raw.get("mode") or "day")
        day, at, ok = 0, None, mode in planner.MODES
        if mode == "day":
            try:
                day = int(raw.get("day"))
            except (TypeError, ValueError):
                day = -1
            ok = day >= 0
        elif mode == "at":
            at = parse_local(raw.get("at"), settings)
            ok = at is not None
        when = _goes(now, {"mode": mode, "day": day, "at": at}, settings) if ok else None
        out.append({"email_no": n, "day": raw.get("day"), "mode": mode,
                    "date": planner.local_day(when, settings=settings) if when else "",
                    "when": when.isoformat() if when else "",
                    "exact": bool(ok and mode != "day" and not (mode == "at" and at < now))})
    return out
