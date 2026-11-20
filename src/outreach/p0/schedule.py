"""P0 schedule: which template goes as which email, on which day after the first."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Event, P0ScheduleRow, P0Template
from . import planner
from .templates import P0Error, _who

logger = logging.getLogger(__name__)

MAX_EMAILS = 10


def get_schedule(session: Session) -> List[Dict[str, Any]]:
    rows = session.execute(
        select(P0ScheduleRow, P0Template.name).join(P0Template, P0Template.id == P0ScheduleRow.template_id)
        .order_by(P0ScheduleRow.email_no)).all()
    return [{"email_no": r.email_no, "template_id": r.template_id, "template_name": name, "day": r.day}
            for r, name in rows]


def _validate(session: Session, rows: List[Dict[str, Any]]) -> List[Dict[str, int]]:
    if not 1 <= len(rows) <= MAX_EMAILS:
        raise P0Error(f"The schedule needs 1 to {MAX_EMAILS} emails.")
    clean: List[Dict[str, int]] = []
    for n, row in enumerate(rows, 1):
        try:
            tid, day = int(row["template_id"]), int(row["day"])
        except (KeyError, TypeError, ValueError):
            raise P0Error(f"Email {n}: choose a template and a day (a whole number).") from None
        tpl = session.get(P0Template, tid)
        if tpl is None:
            raise P0Error(f"Email {n}: that template does not exist.")
        if tpl.status != "live":
            raise P0Error(f"Email {n}: the template must be live (it is {tpl.status}).")
        if day < 0:
            raise P0Error(f"Email {n}: the day cannot be negative.")
        if clean and day <= clean[-1]["day"]:
            raise P0Error(f"Email {n} must go on a later day than email {n - 1}.")
        clean.append({"template_id": tid, "day": day})
    return clean


def save_schedule(session: Session, rows: List[Dict[str, Any]], by: str, now: datetime, *,
                  settings: Any) -> List[Dict[str, Any]]:
    """Replace the whole schedule (email_no = position + 1). Nothing changes if any row is invalid."""
    who = _who(by)
    clean = _validate(session, rows)
    session.execute(delete(P0ScheduleRow))
    session.add_all([P0ScheduleRow(email_no=i, **r) for i, r in enumerate(clean, 1)])
    session.flush()
    planner.replan_all(session, now, settings=settings)
    session.add(Event(channel="system", kind="p0_schedule", occurred_at=now, received_at=now,
                      data={"emails": len(clean), "days": [r["day"] for r in clean], "by": who}))
    session.flush()
    logger.info("p0 schedule saved with %d emails", len(clean))
    return get_schedule(session)


def example_dates(session: Session, now: datetime, *, settings: Any) -> List[Dict[str, Any]]:
    """The dates each email would go if a lead started `now` (business-time calendar days)."""
    return [{"email_no": r["email_no"], "day": r["day"],
             "date": planner.local_day(planner.due_at(now, r["day"], settings=settings), settings=settings)}
            for r in get_schedule(session)]
