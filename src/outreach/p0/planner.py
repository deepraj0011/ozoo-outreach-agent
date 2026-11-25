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


MODES = ("now", "day", "at")


def row_due(day0: datetime, mode: Optional[str], day: int, at: Optional[datetime], *,
            settings: Any) -> Tuple[datetime, bool]:
    """(due time, exact) of one schedule email for a lead whose day 0 is `day0`. "now": day 0 itself, exact.
    "at": that moment, exact, or, for a lead that starts after it, day 0 in working hours (late, never
    skipped). "day": due_at, moved into the send window when handed."""
    if mode == "now":
        return day0, True
    if mode == "at" and at is not None:
        return (at, True) if at >= day0 else (day0, False)
    return due_at(day0, day, settings=settings), False


def goes_at(due: datetime, exact: bool, *, settings: Any, key: str = "") -> datetime:
    """When an email due at `due` actually goes: exactly then, or in the send window's slot."""
    return due if exact else send_slot(due, settings=settings, key=key)


def row_goes(row: "PlannedEmail", *, settings: Any, key: str = "") -> datetime:
    """When a planned-email row goes (or went), as the sender sees it: its send time once sent; once handed,
    the send-window slot of the hand-off (the sender counts from due_at_<k> = hand-off time, so an email handed
    on a Saturday goes on Monday); otherwise the slot of its due time."""
    exact = row.reason == SEND_NOW or bool(row.exact)
    if row.status == "sent" and row.sent_at is not None:
        return row.sent_at
    if row.status == "handed" and row.handed_at is not None:
        return goes_at(row.handed_at, exact, settings=settings, key=key)
    return goes_at(row.due_at, exact, settings=settings, key=key)


def is_late(row: "PlannedEmail", now: datetime, *, settings: Any, key: str = "") -> bool:
    """A planned email due before today, or a handed one the sender has not sent a day after its slot."""
    if row.status == "planned":
        return row.due_at < _day_start(now, settings=settings)
    if row.status == "handed" and row.handed_at is not None:
        return now - row_goes(row, settings=settings, key=key) > _scaled(1, settings)
    return False


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
    rows = []
    for s in _schedule(session):
        due, exact = row_due(day0, s.mode, s.day, s.at, settings=settings)
        rows.append(PlannedEmail(lead_id=lead.id, email_no=s.email_no, template_id=s.template_id,
                                 status="planned", due_at=due, exact=exact))
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
        r.due_at, r.exact = r.due_at + delta, False  # late: the next working-hours slot
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


def lead_day0(lead: Lead) -> Optional[datetime]:
    """The lead's day 0 (flag p0_day0), or None if unreadable."""
    try:
        day0 = datetime.fromisoformat(str((lead.variant_flags or {}).get("p0_day0")))
    except ValueError:
        return None
    return day0 if day0.tzinfo is not None else day0.replace(tzinfo=timezone.utc)


def _replan_lead(session: Session, lead: Lead, sched: List[P0ScheduleRow], now: datetime,
                 *, settings: Any) -> bool:
    day0 = lead_day0(lead)
    if day0 is None:
        logger.warning("p0: lead %s has no readable start day; its emails were not replanned", lead.id)
        return False
    rows = lead_rows(session, lead.id)
    by_no = {r.email_no: r for r in rows}
    gone = {r.email_no for r in rows if r.status in ("handed", "sent")}
    used = {r.template_id for r in rows if r.status in ("handed", "sent")}
    shift = _shift(lead)
    before = [(r.email_no, r.template_id, r.due_at, r.status, r.reason, bool(r.exact)) for r in rows]
    for s in sched:
        row = by_no.get(s.email_no)
        if s.mode in ("now", "at"):
            due, exact = row_due(day0 + shift, s.mode, s.day, s.at, settings=settings)
        else:
            due, exact = due_at(day0, s.day, settings=settings) + shift, False
        skip = s.template_id in used and s.email_no not in gone
        if row is None:
            row = PlannedEmail(lead_id=lead.id, email_no=s.email_no, template_id=s.template_id, due_at=due,
                               exact=exact, status="skipped" if skip else "planned", reason="already_sent" if skip else None)
            session.add(row)
            rows.append(row)
        elif row.status == "planned" and row.reason != SEND_NOW:
            row.template_id, row.due_at, row.exact = s.template_id, due, exact
            if skip:
                row.status, row.reason = "skipped", "already_sent"
    numbers = {s.email_no for s in sched}
    for row in rows:
        if row.status == "planned" and row.email_no not in numbers:
            row.status, row.reason = "cancelled", "removed"
    shift_lead(session, lead, now, settings=settings, rows=rows)
    session.flush()
    after = [(r.email_no, r.template_id, r.due_at, r.status, r.reason, bool(r.exact)) for r in sorted(rows, key=lambda r: r.email_no)]
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
    out: List[Dict[str, Any]] = []
    key = lead.tool_lead_id or ""
    names = dict(session.execute(select(P0Template.id, P0Template.name)).all())
    for r in lead_rows(session, lead.id):
        late = is_late(r, now, settings=settings, key=key)
        goes = goes_at(r.due_at, r.reason == SEND_NOW or bool(r.exact), settings=settings, key=key) \
            if r.status == "sent" else row_goes(r, settings=settings, key=key)  # sent: the day it was due
        out.append({"email_no": r.email_no, "template_name": names.get(r.template_id, ""), "status": r.status,
                    "due_date": local_day(goes, settings=settings),
                    "sent_at": r.sent_at.isoformat() if r.sent_at else None, "reason": r.reason,
                    "sent_subject": r.sent_subject, "late": late,
                    "opens": counts.get((r.email_no, "open"), 0), "clicks": counts.get((r.email_no, "click"), 0),
                    "visits": counts.get((r.email_no, "visit"), 0)})
    return out
