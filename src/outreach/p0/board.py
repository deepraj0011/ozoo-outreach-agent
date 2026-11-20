"""Campaign board for the web app: every P0 lead with one cell per scheduled email, and one email's exact copy.
Read only. Bulk queries (one per table) so the board stays fast with many leads."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event, Lead, P0Template, PlannedEmail
from ..send_plan import _scaled
from ..sequencer import load_templates, render_version
from . import planner
from . import schedule as sch
from .flow import CONTROL_STATES, PAUSED_FLAG
from .templates import P0Error, _version

ENGAGE_KINDS = ("open", "click", "visit")
# event kind -> why the lead's emails stopped, in priority order (a booking beats a reply)
STOP_KINDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("booked", ("booking_created", "booking_rescheduled")),
    ("unsubscribed", ("unsubscribe",)),
    ("bounced", ("bounce",)),
    ("replied", ("reply",)),
)
BOOKED_STATES = ("booked", "handed_off")
OPEN_STATUSES = ("planned", "handed")


def _iso(at: Optional[datetime]) -> Optional[str]:
    return at.isoformat() if at else None


def _slot(row: PlannedEmail, lead: Lead, settings: Any) -> datetime:
    """When the row goes (or went): sent time, else the send-window slot of its due time."""
    if row.status == "sent" and row.sent_at is not None:
        return row.sent_at
    if row.status == "handed" and row.handed_at is not None:
        return row.handed_at
    if row.reason == planner.SEND_NOW:
        return row.due_at
    return planner.send_slot(row.due_at, settings=settings, key=lead.tool_lead_id or "")


def _counts(session: Session, lead_ids: List[int]) -> Dict[Tuple[int, int, str], int]:
    out: Dict[Tuple[int, int, str], int] = {}
    if not lead_ids:
        return out
    q = select(Event.lead_id, Event.step, Event.kind).where(
        Event.lead_id.in_(lead_ids), Event.verdict == "human", Event.kind.in_(ENGAGE_KINDS))
    for lead_id, step, kind in session.execute(q):
        key = (lead_id, step, kind)
        out[key] = out.get(key, 0) + 1
    return out


def _stop_events(session: Session, lead_ids: List[int]) -> Dict[int, Dict[str, datetime]]:
    """{lead_id: {reason: first time it happened}} for replies, unsubscribes, bounces, bookings, manual stops."""
    kinds = [k for _, ks in STOP_KINDS for k in ks] + ["p0_control"]
    reason_of = {k: r for r, ks in STOP_KINDS for k in ks}
    out: Dict[int, Dict[str, datetime]] = {}
    if not lead_ids:
        return out
    q = select(Event.lead_id, Event.kind, Event.occurred_at, Event.data).where(
        Event.lead_id.in_(lead_ids), Event.kind.in_(kinds)).order_by(Event.occurred_at)
    for lead_id, kind, at, data in session.execute(q):
        if kind == "p0_control":
            if (data or {}).get("action") != "stop":
                continue
            reason = "stopped"
        else:
            reason = reason_of[kind]
        out.setdefault(lead_id, {}).setdefault(reason, at)
    return out


def _stop(lead: Lead, rows: List[PlannedEmail], events: Dict[str, datetime]) -> Tuple[Optional[str], Optional[datetime]]:
    """Why the lead's emails stopped (None while they still go, or after all of them went)."""
    cancelled = any(r.status == "cancelled" and r.reason != "removed" for r in rows)
    if lead.state in CONTROL_STATES and not cancelled:
        return None, None
    if lead.state in BOOKED_STATES and "booked" not in events:
        return "booked", None
    for reason, _ in STOP_KINDS:
        if reason in events:
            return reason, events[reason]
    if cancelled or "stopped" in events:
        return "stopped", events.get("stopped")
    return None, None


def _cell(row: PlannedEmail, lead: Lead, counts: Dict[Tuple[int, int, str], int], today: str,
          day_start: datetime, now: datetime, settings: Any) -> Dict[str, Any]:
    slot = _slot(row, lead, settings)
    status = row.status
    if status in OPEN_STATUSES and planner.local_day(slot, settings=settings) == today:
        status = "today"
    late = (row.status == "planned" and row.due_at < day_start) or (
        row.status == "handed" and row.handed_at is not None and now - row.handed_at > _scaled(1, settings))
    return {"email_no": row.email_no, "status": status, "date": _iso(slot), "sent_at": _iso(row.sent_at),
            "opens": counts.get((lead.id, row.email_no, "open"), 0),
            "clicks": counts.get((lead.id, row.email_no, "click"), 0),
            "visits": counts.get((lead.id, row.email_no, "visit"), 0),
            "reason": row.reason, "late": late}


def board(session: Session, now: datetime, *, settings: Any) -> Dict[str, Any]:
    templates = list(session.execute(select(P0Template.id, P0Template.name, P0Template.status)
                                     .order_by(P0Template.id)).all())
    names = {t.id: t.name for t in templates}
    schedule = [{"email_no": r["email_no"], "day": r["day"], "template_id": r["template_id"],
                 "template_name": names.get(r["template_id"], "")} for r in sch.get_schedule(session)]
    leads = [ld for ld in session.scalars(select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()))
             if (ld.variant_flags or {}).get("p0")]
    ids = [ld.id for ld in leads]
    rows: Dict[int, List[PlannedEmail]] = {}
    if ids:
        for r in session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id.in_(ids))
                                 .order_by(PlannedEmail.email_no)):
            rows.setdefault(r.lead_id, []).append(r)
    counts, stops = _counts(session, ids), _stop_events(session, ids)
    today = planner.local_day(now, settings=settings)
    day_start = planner._day_start(now, settings=settings)
    out_leads: List[Dict[str, Any]] = []
    totals = {"leads": len(leads), "sent": 0, "today": 0, "replied": 0, "stopped": 0, "unsubscribed": 0}
    for ld in leads:
        mine = rows.get(ld.id, [])
        cells = [_cell(r, ld, counts, today, day_start, now, settings) for r in mine]
        reason, at = _stop(ld, mine, stops.get(ld.id, {}))
        totals["sent"] += sum(1 for c in cells if c["status"] == "sent")
        totals["today"] += sum(1 for c in cells if c["status"] == "today")
        totals["replied"] += reason == "replied"
        totals["unsubscribed"] += reason == "unsubscribed"
        totals["stopped"] += reason is not None
        out_leads.append({"id": ld.id, "name": ld.name, "company": ld.company, "role": ld.role or "",
                          "state": ld.state, "paused": bool((ld.variant_flags or {}).get(PAUSED_FLAG)),
                          "stop_reason": reason, "stopped_at": _iso(at), "emails": cells})
    return {"schedule": schedule, "templates": [{"id": t.id, "name": t.name, "status": t.status} for t in templates],
            "totals": totals, "leads": out_leads, "now": now.isoformat(),
            "example": sch.example_dates(session, now, settings=settings)}


def _render(lead: Lead, tpl: P0Template, email_no: int, settings: Any) -> Dict[str, str]:
    """The template for this lead, rendered as a preview: plain page links, not tracked ones (a click in the
    web app must not count as the lead's click)."""
    out = render_version(lead, _version(tpl), email_no, templates=load_templates(), settings=settings, preview=True)
    out["html"] = (out.get("html") or "").replace(settings.link_base_url.rstrip("/") + "/img/", "/img/")
    return out


def email_detail(session: Session, lead_id: int, email_no: int, now: datetime, *, settings: Any) -> Dict[str, Any]:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise P0Error("Lead not found.")
    row = session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead_id,
                                                     PlannedEmail.email_no == email_no)).first()
    if row is None:
        raise P0Error("This lead has no such email.")
    tpl = session.get(P0Template, row.template_id)
    rendered = _render(lead, tpl, email_no, settings) if tpl is not None else None
    counts = _counts(session, [lead_id])
    stored = row.status in ("sent", "handed") and row.sent_subject is not None
    if stored:
        subject, text = row.sent_subject or "", row.sent_text or ""
        html = rendered["html"] if rendered else ""
    elif rendered is not None:
        subject, text, html = rendered["subject"], rendered["text"], rendered["html"]
    else:
        subject, text, html = "", "", ""
    return {"email_no": email_no, "subject": subject, "text": text, "html": html,
            "html_is_current_template": bool(stored and html),
            "template_name": tpl.name if tpl is not None else "", "status": row.status, "reason": row.reason,
            "when": _iso(_slot(row, lead, settings)), "sent_at": _iso(row.sent_at),
            "opens": counts.get((lead_id, email_no, "open"), 0), "clicks": counts.get((lead_id, email_no, "click"), 0),
            "visits": counts.get((lead_id, email_no, "visit"), 0)}
