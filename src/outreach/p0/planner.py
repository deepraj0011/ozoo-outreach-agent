"""P0 planner: when an email is due, and which calendar day that is."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

import logging

from ..models import Event, Lead, P0ScheduleRow, P0Template, PlannedEmail
from ..send_plan import _scaled
from ..states import TERMINAL_VALUES

logger = logging.getLogger(__name__)

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


def send_slot(at: datetime, *, settings: Any, key: str = "") -> datetime:
    """When our sender would actually send an email due at `at`: the send window's first-email slot
    (every P0 email uses it), or `at` itself without a window or on a fast test clock."""
    from ..sendwindow import window_from_settings
    window = window_from_settings(settings)
    if window is None or _scale(settings) != 1:
        return at
    return window.slot(at, 1, key)


def _day_start(now: datetime, *, settings: Any) -> datetime:
    scale = _scale(settings)
    if scale == 1:
        midnight = datetime.combine(now.astimezone(_zone(settings)).date(), time.min, tzinfo=_zone(settings))
        return midnight.astimezone(timezone.utc)
    length = timedelta(days=1) / scale
    return EPOCH + int((now - EPOCH) / length) * length


SHIFT_FLAG = "p0_shift_s"  # seconds the lead's planned emails were moved later (resume, hold end)
SEND_NOW = "send_now"  # a row the product manager sent at once: replans leave it alone


def lead_rows(session: Session, lead_id: int) -> List[PlannedEmail]:
    """The lead's planned-email rows, in email order (shared by planner and flow)."""
    return list(session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead_id)
                                .order_by(PlannedEmail.email_no)))


def _schedule(session: Session) -> List[P0ScheduleRow]:
    return list(session.scalars(select(P0ScheduleRow).order_by(P0ScheduleRow.email_no)))


def plan_lead(session: Session, lead: Lead, day0: datetime, *, settings: Any) -> List[PlannedEmail]:
    """Give the lead one planned row per schedule email. Does nothing if it already has rows."""
    existing = lead_rows(session, lead.id)
    if existing:
        return existing
    flags = dict(lead.variant_flags or {})
    flags["p0"], flags["p0_day0"] = True, day0.isoformat()
    lead.variant_flags = flags
    rows = [PlannedEmail(lead_id=lead.id, email_no=s.email_no, template_id=s.template_id, status="planned",
                         due_at=due_at(day0, s.day, settings=settings)) for s in _schedule(session)]
    session.add_all(rows)
    session.flush()
    return rows


def shift_overdue(rows: Sequence[PlannedEmail], now: datetime, *, settings: Any) -> timedelta:
    """If the next planned email is due before today, move it (and the later ones) so it is due now.
    Returns how far they moved (zero if not)."""
    planned = sorted((r for r in rows if r.status == "planned"), key=lambda r: r.email_no)
    if not planned or planned[0].due_at >= _day_start(now, settings=settings):
        return timedelta(0)
    delta = now - planned[0].due_at
    for r in planned:
        r.due_at = r.due_at + delta
    return delta


def _shift(lead: Lead) -> timedelta:
    try:
        return timedelta(seconds=float((lead.variant_flags or {}).get(SHIFT_FLAG) or 0))
    except (TypeError, ValueError):
        return timedelta(0)


def shift_lead(session: Session, lead: Lead, now: datetime, *, settings: Any,
               rows: Optional[Sequence[PlannedEmail]] = None) -> timedelta:
    """shift_overdue for one lead, remembered (flag p0_shift_s) so a later schedule save keeps it."""
    delta = shift_overdue(lead_rows(session, lead.id) if rows is None else rows, now, settings=settings)
    if delta:
        lead.variant_flags = {**(lead.variant_flags or {}),
                              SHIFT_FLAG: (_shift(lead) + delta).total_seconds()}
        session.flush()
    return delta


def _replan_lead(session: Session, lead: Lead, sched: List[P0ScheduleRow], now: datetime,
                 *, settings: Any) -> bool:
    try:
        day0 = datetime.fromisoformat(str((lead.variant_flags or {}).get("p0_day0")))
    except ValueError:
        logger.warning("p0: lead %s has no readable start day; its emails were not replanned", lead.id)
        return False
    if day0.tzinfo is None:
        day0 = day0.replace(tzinfo=timezone.utc)
    rows = lead_rows(session, lead.id)
    by_no = {r.email_no: r for r in rows}
    gone = {r.email_no for r in rows if r.status in ("handed", "sent")}
    used = {r.template_id for r in rows if r.status in ("handed", "sent")}
    shift = _shift(lead)
    before = [(r.email_no, r.template_id, r.due_at, r.status, r.reason) for r in rows]
    for s in sched:
        row = by_no.get(s.email_no)
        due = due_at(day0, s.day, settings=settings) + shift
        skip = s.template_id in used and s.email_no not in gone
        if row is None:
            row = PlannedEmail(lead_id=lead.id, email_no=s.email_no, template_id=s.template_id, due_at=due,
                               status="skipped" if skip else "planned", reason="already_sent" if skip else None)
            session.add(row)
            rows.append(row)
        elif row.status == "planned" and row.reason != SEND_NOW:
            row.template_id, row.due_at = s.template_id, due
            if skip:
                row.status, row.reason = "skipped", "already_sent"
    numbers = {s.email_no for s in sched}
    for row in rows:
        if row.status == "planned" and row.email_no not in numbers:
            row.status, row.reason = "cancelled", "removed"
    shift_lead(session, lead, now, settings=settings, rows=rows)
    session.flush()
    after = [(r.email_no, r.template_id, r.due_at, r.status, r.reason) for r in sorted(rows, key=lambda r: r.email_no)]
    return sorted(before) != after


def replan_all(session: Session, now: datetime, *, settings: Any) -> int:
    """Apply the current schedule to every P0 lead that still has planned emails (in the sequence, paused
    or on hold). Returns leads changed."""
    sched = _schedule(session)
    changed = 0
    planned = select(PlannedEmail.lead_id).where(PlannedEmail.status == "planned")
    leads = session.scalars(select(Lead).where(Lead.state.not_in(TERMINAL_VALUES), Lead.id.in_(planned))
                            .order_by(Lead.id))
    for lead in leads.all():
        if (lead.variant_flags or {}).get("p0") and _replan_lead(session, lead, sched, now, settings=settings):
            changed += 1
    return changed


def next_row(session: Session, lead: Lead) -> Optional[PlannedEmail]:
    return session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead.id,
                                                      PlannedEmail.status == "planned")
                           .order_by(PlannedEmail.email_no).limit(1)).first()


def email_counts(session: Session, lead_ids: Sequence[int]) -> Dict[int, Tuple[int, int]]:
    """{lead_id: (emails sent, emails sent or still to go)} for the lead panel."""
    out: Dict[int, Tuple[int, int]] = {}
    if not lead_ids:
        return out
    for lead_id, status in session.execute(select(PlannedEmail.lead_id, PlannedEmail.status).where(
            PlannedEmail.lead_id.in_(list(lead_ids)), PlannedEmail.status.in_(("planned", "handed", "sent")))):
        sent, total = out.get(lead_id, (0, 0))
        out[lead_id] = (sent + (status == "sent"), total + 1)
    return out


def cancel_remaining(session: Session, lead: Lead, reason: str, now: datetime) -> int:
    n = 0
    for row in lead_rows(session, lead.id):
        if row.status in ("planned", "handed"):
            row.status, row.reason = "cancelled", reason
            n += 1
    session.flush()
    return n


def mark_sent(session: Session, lead: Lead, tool_step: int, now: datetime) -> Optional[PlannedEmail]:
    row = session.scalars(select(PlannedEmail).where(
        PlannedEmail.lead_id == lead.id, PlannedEmail.status == "handed",
        PlannedEmail.tool_step == tool_step)).first()
    if row is not None:
        row.status, row.sent_at = "sent", now
        session.flush()
    return row


def lead_view(session: Session, lead: Lead, now: datetime, *, settings: Any) -> List[Dict[str, Any]]:
    counts: Dict[Any, int] = {}
    for step, kind in session.execute(select(Event.step, Event.kind).where(
            Event.lead_id == lead.id, Event.verdict == "human", Event.kind.in_(("open", "click", "visit")))):
        counts[(step, kind)] = counts.get((step, kind), 0) + 1
    start, out = _day_start(now, settings=settings), []
    names = dict(session.execute(select(P0Template.id, P0Template.name)).all())
    for r in lead_rows(session, lead.id):
        late = (r.status == "planned" and r.due_at < start) or (
            r.status == "handed" and r.handed_at is not None and now - r.handed_at > _scaled(1, settings))
        out.append({"email_no": r.email_no, "template_name": names.get(r.template_id, ""), "status": r.status,
                    "due_date": local_day(
                        r.due_at if r.reason == SEND_NOW else send_slot(r.due_at, settings=settings,
                                                                        key=lead.tool_lead_id or ""),
                        settings=settings), "sent_at": r.sent_at, "reason": r.reason,
                    "sent_subject": r.sent_subject, "late": late,
                    "opens": counts.get((r.email_no, "open"), 0), "clicks": counts.get((r.email_no, "click"), 0),
                    "visits": counts.get((r.email_no, "visit"), 0)})
    return out
