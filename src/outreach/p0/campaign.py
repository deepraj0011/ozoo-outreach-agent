"""P0 campaign switch: nothing is handed to the sender until the product manager presses Start.
Leads still get planned rows while it is off (the board shows them); Send now on one lead still works."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Event, Lead, P0Campaign, PlannedEmail
from . import planner
from .flow import CONTROL_STATES, PAUSED_FLAG
from .templates import P0Error, _who

logger = logging.getLogger(__name__)

CAMPAIGN_ID = 1
# a brand-new system waits for Start; an upgraded one that already handed or sent a P0 email keeps sending.
# tests written before Start existed set this True (tests/conftest.py)
FRESH_RUNNING = False
CAMPAIGN_PAUSED = "p0_campaign_paused"  # the campaign Pause held this lead in the sender; Start releases it


def get(session: Session) -> P0Campaign:
    """The campaign row, created on first use (running only if a P0 email already went to the sender)."""
    row = session.get(P0Campaign, CAMPAIGN_ID)
    if row is not None:
        return row
    first = session.scalar(select(func.min(PlannedEmail.handed_at)).where(
        PlannedEmail.status.in_(("handed", "sent"))))
    row = P0Campaign(id=CAMPAIGN_ID, running=first is not None or FRESH_RUNNING, started_at=first)
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:  # another request created it meanwhile
        row = session.get(P0Campaign, CAMPAIGN_ID)
    return row


def is_running(session: Session) -> bool:
    return bool(get(session).running)


def _leads(session: Session) -> List[Lead]:
    """P0 leads still in the sequence (or on hold) with emails to go or waiting in the sender."""
    open_rows = select(PlannedEmail.lead_id).where(PlannedEmail.status.in_(("planned", "handed")))
    return [ld for ld in session.scalars(select(Lead).where(Lead.state.in_(CONTROL_STATES),
                                                            Lead.id.in_(open_rows)).order_by(Lead.id))
            if (ld.variant_flags or {}).get("p0")]


def view(session: Session) -> Dict[str, Any]:
    row = get(session)
    return {"running": bool(row.running), "started_at": row.started_at.isoformat() if row.started_at else None,
            "leads": len(_leads(session))}


def _log(session: Session, action: str, who: str, now: datetime, leads: int) -> None:
    session.add(Event(channel="system", kind="p0_campaign", occurred_at=now, received_at=now, verdict="n/a",
                      processed=True, data={"action": action, "by": who, "leads": leads}))
    session.flush()
    logger.info("p0 campaign %s (%d leads)", action, leads)


def start(session: Session, tool: Any, by: str, now: datetime, *, settings: Any) -> Dict[str, Any]:
    """Start (or resume) sending. A lead that has had nothing sent and whose day 0 has passed starts now
    (its planned rows recomputed); the others have overdue emails moved to now, keeping their gaps."""
    who = _who(by)
    row = get(session)
    if row.running:
        raise P0Error("Sending has already started.")
    row.running, row.started_at, row.updated_by, row.updated_at = True, now, who, now
    sched = planner._schedule(session)
    leads = _leads(session)
    for lead in leads:
        flags = dict(lead.variant_flags or {})
        if flags.pop(CAMPAIGN_PAUSED, None):
            lead.variant_flags = flags
            if lead.tool_lead_id and not flags.get(PAUSED_FLAG):
                tool.resume(lead.tool_lead_id)
        rows = planner.lead_rows(session, lead.id)
        day0 = planner.lead_day0(lead)
        if not any(r.status in ("handed", "sent") for r in rows) and day0 is not None and day0 < now:
            flags = dict(lead.variant_flags or {})
            flags["p0_day0"] = now.isoformat()
            flags.pop(planner.SHIFT_FLAG, None)
            lead.variant_flags = flags
            planner._replan_lead(session, lead, sched, now, settings=settings)
        else:
            planner.shift_lead(session, lead, now, settings=settings, rows=rows)
    _log(session, "start", who, now, len(leads))
    return view(session)


def pause(session: Session, tool: Any, by: str, now: datetime) -> Dict[str, Any]:
    """Stop handing emails; an email already waiting in the sender is held there too."""
    who = _who(by)
    row = get(session)
    if not row.running:
        raise P0Error("Sending is not running.")
    row.running, row.updated_by, row.updated_at = False, who, now
    leads = _leads(session)
    for lead in leads:
        flags = dict(lead.variant_flags or {})
        handed = any(r.status == "handed" for r in planner.lead_rows(session, lead.id))
        if handed and lead.tool_lead_id and not flags.get(PAUSED_FLAG):
            tool.pause(lead.tool_lead_id)
            lead.variant_flags = {**flags, CAMPAIGN_PAUSED: True}
    _log(session, "pause", who, now, len(leads))
    return view(session)
