"""P0 flow: start a lead on the schedule and hand its due emails to our own sender, one at a time."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..emailtool.mailpit import MAX_STEPS
from ..models import Job, P0Template, PlannedEmail
from ..states import LeadState
from . import planner

logger = logging.getLogger(__name__)

PAUSED_FLAG = "p0_paused"
SEND_NOW = "send_now"


def is_p0(settings: Any, tool: Any) -> bool:
    """P0 applies when switched on and the tool sends on our dates (our own sender)."""
    return getattr(settings, "email_flow", "classic") == "p0" and bool(getattr(tool, "SUPPORTS_SCHEDULE", False))


def _rows(session: Session, lead_id: int) -> List[PlannedEmail]:
    return list(session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead_id)
                                .order_by(PlannedEmail.email_no)))


def _sent_today(rows: List[PlannedEmail], now: datetime, settings: Any) -> bool:
    today = planner.local_day(now, settings=settings)
    return any(at is not None and planner.local_day(at, settings=settings) == today
               for r in rows for at in (r.handed_at, r.sent_at))


def dispatch_due(session: Session, lead: Any, tool: Any, now: datetime, *, settings: Any) -> bool:
    """Hand the lead's next due email to the tool (rendered now). At most one email per calendar day
    unless the product manager pressed Send next now. Written in the caller's transaction."""
    from ..sequencer import _tracking_vars  # sequencer imports this module
    from .templates import render

    flags = lead.variant_flags or {}
    if flags.get(PAUSED_FLAG) or lead.state != LeadState.IN_SEQUENCE.value or not lead.tool_lead_id:
        return False
    rows = _rows(session, lead.id)
    if any(r.status == "handed" for r in rows):
        return False
    if session.scalar(select(Job.id).where(Job.lead_id == lead.id, Job.kind == "classify",
                                           Job.status.in_(("pending", "running"))).limit(1)) is not None:
        return False  # a reply is being read: its label decides whether the emails go on
    row = next((r for r in rows if r.status == "planned"), None)
    if row is None or row.due_at > now:
        return False
    if row.reason != SEND_NOW and _sent_today(rows, now, settings):
        return False
    tpl = session.get(P0Template, row.template_id)
    if tpl is None:
        row.status, row.reason = "skipped", "no_template"
        session.flush()
        return False
    out = render(lead, tpl, row.email_no, settings=settings)
    k = sum(1 for r in rows if r.status in ("handed", "sent")) + 1
    later = sum(1 for r in rows if r.status == "planned" and r.email_no > row.email_no)
    variables = {f"subject_{k}": out["subject"], f"body_{k}": out["text"], f"html_{k}": out.get("html") or "",
                 f"due_at_{k}": now.isoformat(), "steps": str(min(k + later, MAX_STEPS)),
                 **_tracking_vars(lead, k, row.email_no, settings)}
    if row.reason == SEND_NOW:
        variables[f"due_exact_{k}"] = "1"
    tool.update_variables(lead.tool_lead_id, variables)
    row.status, row.tool_step, row.handed_at = "handed", k, now
    row.sent_subject, row.sent_text = out["subject"], out["text"]
    session.flush()
    logger.info("p0: lead %s email %s handed to the tool as step %s", lead.id, row.email_no, k)
    return True


# ------------------------------------------------------------------ per-lead controls (design 4.6)

def _log(session: Session, lead: Any, action: str, by: str, now: datetime) -> None:
    from ..models import Event
    session.add(Event(lead_id=lead.id, channel="system", kind="p0_control", occurred_at=now, received_at=now,
                      verdict="n/a", processed=True, data={"action": action, "by": by}))
    session.flush()
    logger.info("p0: lead %s %s", lead.id, action)


def _set_paused(lead: Any, paused: bool) -> None:
    flags = dict(lead.variant_flags or {})
    if paused:
        flags[PAUSED_FLAG] = True
    else:
        flags.pop(PAUSED_FLAG, None)
    lead.variant_flags = flags


def pause(session: Session, lead: Any, tool: Any, by: str, now: datetime) -> None:
    """Planned emails wait; an email already handed to the tool is held there too."""
    _set_paused(lead, True)
    if lead.tool_lead_id and any(r.status == "handed" for r in _rows(session, lead.id)):
        tool.pause(lead.tool_lead_id)
    _log(session, lead, "pause", by, now)


def resume(session: Session, lead: Any, tool: Any, by: str, now: datetime, *, settings: Any) -> None:
    """Overdue emails go in the next slot, the rest keep their gaps."""
    _set_paused(lead, False)
    planner.shift_overdue(_rows(session, lead.id), now, settings=settings)
    if lead.tool_lead_id:
        tool.resume(lead.tool_lead_id)
    _log(session, lead, "resume", by, now)


def stop(session: Session, lead: Any, deps: Any, by: str, now: datetime) -> None:
    """Cancel the rest (ToolStop cancels the planned rows) and move the lead to nurture."""
    from ..actions import SetState, ToolStop, execute  # actions imports p0 lazily too
    execute(session, lead, [ToolStop(), SetState(LeadState.NURTURE, "p0_stopped")], deps, now)
    _log(session, lead, "stop", by, now)


def send_now(session: Session, lead: Any, tool: Any, by: str, now: datetime, *, settings: Any) -> bool:
    """Hand the next planned email at once (exact time, even if one already went today)."""
    row = planner.next_row(session, lead)
    if row is None:
        return False
    row.due_at, row.reason = now, SEND_NOW
    session.flush()
    _log(session, lead, "send_now", by, now)
    return dispatch_due(session, lead, tool, now, settings=settings)
