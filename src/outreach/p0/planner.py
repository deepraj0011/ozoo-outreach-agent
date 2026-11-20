"""P0 planner: when an email is due, and which calendar day that is."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event, Lead, P0ScheduleRow, P0Template, PlannedEmail
from ..send_plan import _scaled
from ..states import LeadState

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


def _day_start(now: datetime, *, settings: Any) -> datetime:
    scale = _scale(settings)
    if scale == 1:
        midnight = datetime.combine(now.astimezone(_zone(settings)).date(), time.min, tzinfo=_zone(settings))
        return midnight.astimezone(timezone.utc)
    length = timedelta(days=1) / scale
    return EPOCH + int((now - EPOCH) / length) * length


def _rows(session: Session, lead_id: int) -> List[PlannedEmail]:
    return list(session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead_id)
                                .order_by(PlannedEmail.email_no)))


def _schedule(session: Session) -> List[P0ScheduleRow]:
    return list(session.scalars(select(P0ScheduleRow).order_by(P0ScheduleRow.email_no)))


def plan_lead(session: Session, lead: Lead, day0: datetime, *, settings: Any) -> List[PlannedEmail]:
    """Give the lead one planned row per schedule email. Does nothing if it already has rows."""
    existing = _rows(session, lead.id)
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


def shift_overdue(rows: Sequence[PlannedEmail], now: datetime, *, settings: Any) -> None:
    """If the next planned email is due before today, move it (and the later ones) so it is due now."""
    planned = sorted((r for r in rows if r.status == "planned"), key=lambda r: r.email_no)
    if not planned or planned[0].due_at >= _day_start(now, settings=settings):
        return
    delta = now - planned[0].due_at
    for r in planned:
        r.due_at = r.due_at + delta


def _replan_lead(session: Session, lead: Lead, sched: List[P0ScheduleRow], now: datetime,
                 *, settings: Any) -> bool:
    try:
        day0 = datetime.fromisoformat(str(lead.variant_flags.get("p0_day0")))
    except ValueError:
        return False
    if day0.tzinfo is None:
        day0 = day0.replace(tzinfo=timezone.utc)
    rows = _rows(session, lead.id)
    by_no = {r.email_no: r for r in rows}
    gone = {r.email_no for r in rows if r.status in ("handed", "sent")}
    used = {r.template_id for r in rows if r.status in ("handed", "sent")}
    before = [(r.email_no, r.template_id, r.due_at, r.status, r.reason) for r in rows]
    for s in sched:
        row = by_no.get(s.email_no)
        due = due_at(day0, s.day, settings=settings)
        skip = s.template_id in used and s.email_no not in gone
        if row is None:
            row = PlannedEmail(lead_id=lead.id, email_no=s.email_no, template_id=s.template_id, due_at=due,
                               status="skipped" if skip else "planned", reason="already_sent" if skip else None)
            session.add(row)
            rows.append(row)
        elif row.status == "planned":
            row.template_id, row.due_at = s.template_id, due
            if skip:
                row.status, row.reason = "skipped", "already_sent"
    numbers = {s.email_no for s in sched}
    for row in rows:
        if row.status == "planned" and row.email_no not in numbers:
            row.status, row.reason = "cancelled", "removed"
    shift_overdue(rows, now, settings=settings)
    session.flush()
    after = [(r.email_no, r.template_id, r.due_at, r.status, r.reason) for r in sorted(rows, key=lambda r: r.email_no)]
    return sorted(before) != after


def replan_all(session: Session, now: datetime, *, settings: Any) -> int:
    """Apply the current schedule to every lead in the sequence that is on P0. Returns leads changed."""
    sched = _schedule(session)
    changed = 0
    leads = session.scalars(select(Lead).where(Lead.state == LeadState.IN_SEQUENCE.value))
    for lead in leads.all():
        if (lead.variant_flags or {}).get("p0") and _replan_lead(session, lead, sched, now, settings=settings):
            changed += 1
    return changed


def next_row(session: Session, lead: Lead) -> Optional[PlannedEmail]:
    return session.scalars(select(PlannedEmail).where(PlannedEmail.lead_id == lead.id,
                                                      PlannedEmail.status == "planned")
                           .order_by(PlannedEmail.email_no).limit(1)).first()


def cancel_remaining(session: Session, lead: Lead, reason: str, now: datetime) -> int:
    n = 0
    for row in _rows(session, lead.id):
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
    for r in _rows(session, lead.id):
        late = (r.status == "planned" and r.due_at < start) or (
            r.status == "handed" and r.handed_at is not None and now - r.handed_at > _scaled(1, settings))
        out.append({"email_no": r.email_no, "template_name": names.get(r.template_id, ""), "status": r.status,
                    "due_date": local_day(r.due_at, settings=settings), "sent_at": r.sent_at, "reason": r.reason,
                    "sent_subject": r.sent_subject, "late": late,
                    "opens": counts.get((r.email_no, "open"), 0), "clicks": counts.get((r.email_no, "click"), 0),
                    "visits": counts.get((r.email_no, "visit"), 0)})
    return out
