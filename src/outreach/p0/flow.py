"""P0 flow: start a lead on the schedule and hand its due emails to our own sender, one at a time."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..emailtool.mailpit import MAX_STEPS
from ..models import Job, P0Template, PlannedEmail
from ..states import LeadState
from . import planner

logger = logging.getLogger(__name__)

PAUSED_FLAG = "p0_paused"
SEND_NOW = planner.SEND_NOW
# the tool never ends a P0 lead by itself (p0_finish stops it), so an email added to the schedule later still
# goes; every step is sent at the first-email slot of the send window (no afternoon / morning rotation)
TOOL_VARS = {"plan": "1", "steps": str(MAX_STEPS), "p0": "1"}
CONTROL_STATES = (LeadState.IN_SEQUENCE.value, LeadState.PAUSED.value)


def is_p0(settings: Any, tool: Any) -> bool:
    """P0 applies when switched on and the tool sends on our dates (our own sender)."""
    return getattr(settings, "email_flow", "classic") == "p0" and bool(getattr(tool, "SUPPORTS_SCHEDULE", False))


def start_p0(session: Session, lead: Any, now: datetime, *, settings: Any) -> List[PlannedEmail]:
    """Put the lead on the schedule: the starter templates exist, day 0 is the start time picked at import
    (flag plan_start) or now, whichever is later (a stale import date never sends a backlog at once)."""
    from .templates import ensure_starter
    ensure_starter(session, now, settings=settings)
    day0 = now
    raw = (lead.variant_flags or {}).get("plan_start")
    if raw:
        try:
            picked = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            picked = now
        if picked.tzinfo is None:
            picked = picked.replace(tzinfo=timezone.utc)
        day0 = max(picked, now)
    return planner.plan_lead(session, lead, day0, settings=settings)


def _sent_today(rows: List[PlannedEmail], now: datetime, settings: Any) -> bool:
    today = planner.local_day(now, settings=settings)
    return any(at is not None and planner.local_day(at, settings=settings) == today
               for r in rows for at in (r.handed_at, r.sent_at))


def _blocked(session: Session, lead: Any, rows: List[PlannedEmail]) -> Optional[str]:
    """Why no email can be handed to the sender now (None = it can)."""
    if (lead.variant_flags or {}).get(PAUSED_FLAG):
        return "This lead's emails are paused: resume them first."
    if lead.state == LeadState.PAUSED.value:
        return "This lead is on hold, so no email can go now."
    if lead.state != LeadState.IN_SEQUENCE.value:
        return "This lead is no longer in the email sequence."
    if not lead.tool_lead_id:
        return "This lead is not in the sender yet."
    if any(r.status == "handed" for r in rows):
        return "An email is already waiting in the sender for this lead."
    if session.scalar(select(Job.id).where(Job.lead_id == lead.id, Job.kind == "classify",
                                           Job.status.in_(("pending", "running"))).limit(1)) is not None:
        return "A reply from this lead is being read: wait for it."  # its label decides whether emails go on
    return None


def dispatch_due(session: Session, lead: Any, tool: Any, now: datetime, *, settings: Any,
                 force: bool = False) -> bool:
    """Hand the lead's next due email to the tool (rendered now), only while the campaign runs (p0.campaign).
    At most one email per calendar day, except
    exact rows (the PM picked that moment: "now", a date and time, Send now), which the tool sends at once.
    force (Send now): the next planned email goes at once at that exact time; if it cannot, P0Error says
    why and nothing changes. Written in the caller's transaction."""
    from ..sequencer import _tracking_vars  # sequencer imports this module
    from . import campaign  # campaign imports this module
    from .templates import P0Error, render

    if not campaign.is_running(session) and not force:
        return False  # nothing is handed before the PM presses Start (Send now on one lead still works)
    rows = planner.lead_rows(session, lead.id)
    why = _blocked(session, lead, rows)
    row = next((r for r in rows if r.status == "planned"), None)
    if why is None and row is None:
        why = "There is no email left to send to this lead."
    if force:
        if why is not None:
            raise P0Error(why)
        row.due_at, row.reason = now, SEND_NOW
    # an exact row (Send now, "now", a date and time) goes at its moment even if one already went today
    exact = row is not None and (row.reason == SEND_NOW or bool(row.exact))
    if not force and (why is not None or row.due_at > now or (not exact and _sent_today(rows, now, settings))):
        return False
    tpl = session.get(P0Template, row.template_id)
    if tpl is None:
        row.status, row.reason = "skipped", "no_template"
        session.flush()
        return False
    out = render(lead, tpl, row.email_no, settings=settings)
    k = sum(1 for r in rows if r.status in ("handed", "sent")) + 1
    variables = {f"subject_{k}": out["subject"], f"body_{k}": out["text"], f"html_{k}": out.get("html") or "",
                 f"due_at_{k}": now.isoformat(), **TOOL_VARS, **_tracking_vars(lead, k, row.email_no, settings)}
    if exact:
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


def _require(lead: Any) -> None:
    """The per-lead controls are only for P0 leads still in the sequence (or on hold)."""
    from .templates import P0Error
    if not (lead.variant_flags or {}).get("p0"):
        raise P0Error("This lead is not on the email schedule.")
    if lead.state not in CONTROL_STATES:
        raise P0Error(f"This lead is {lead.state}: its scheduled emails have ended.")


def pause(session: Session, lead: Any, tool: Any, by: str, now: datetime) -> None:
    """Planned emails wait; the sender holds the lead too (an email already handed stays there)."""
    _require(lead)
    _set_paused(lead, True)
    if lead.tool_lead_id:
        tool.pause(lead.tool_lead_id)
    _log(session, lead, "pause", by, now)


def resume(session: Session, lead: Any, tool: Any, by: str, now: datetime, *, settings: Any) -> None:
    """Overdue emails go in the next slot, the rest keep their gaps."""
    _require(lead)
    _set_paused(lead, False)
    planner.shift_lead(session, lead, now, settings=settings)
    if lead.tool_lead_id:
        tool.resume(lead.tool_lead_id)
    _log(session, lead, "resume", by, now)


def stop(session: Session, lead: Any, deps: Any, by: str, now: datetime) -> None:
    """Cancel the rest (ToolStop cancels the planned rows) and move the lead to nurture."""
    from ..actions import SetState, ToolStop, execute  # actions imports p0 lazily too
    _require(lead)
    execute(session, lead, [ToolStop(), SetState(LeadState.NURTURE, "p0_stopped")], deps, now)
    _log(session, lead, "stop", by, now)


def send_now(session: Session, lead: Any, tool: Any, by: str, now: datetime, *, settings: Any) -> bool:
    """Hand the next planned email at once (exact time, even if one already went today). P0Error with the
    reason if it cannot go now (paused, on hold, an email already waiting in the sender, a reply being read)."""
    _require(lead)
    sent = dispatch_due(session, lead, tool, now, settings=settings, force=True)
    _log(session, lead, "send_now", by, now)
    return sent
