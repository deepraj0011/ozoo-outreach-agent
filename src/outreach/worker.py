"""The worker tick: every time-driven step of the outreach flow (plan Task 12).

tick() runs the jobs below in order, each in its own session and transaction. A failing job is
logged (exception type only), recorded as an integration_error and never stops the jobs after it.
Jobs that touch leads handle at most `batch` leads per tick (default 50), oldest first, and commit
per lead, so one bad lead does not hold up the rest.

  start_new                  IN_SEQUENCE leads not started yet -> outreach.start_outreach
  process_settled_clicks     clicks older than 30 s: bot filter verdict, then decide/execute (points)
  booking_click_not_booked   30 min after a human booking click with no booking: slots reply, once
  wa_unconfirmed             template sends with an unknown outcome: confirm from status webhooks
  wa_reminder                WhatsApp intro 48 h ago, no inbound, no reminder -> `ozoo_reminder`
  wa_no_response             reminder 48 h ago, no inbound -> switch_channel("wa_no_response")
  resume_paused              PAUSED("ooo") whose pause_until has passed -> back in sequence
  account_holds              holds.resume_account_holds (every tick)
  eu_holds                   holds.release_eu_holds (only when EU_EMAIL_APPROVED)
  end_of_sequence            Email 4 sent >= 5 days ago -> end_of_sequence_route
  wa_sla                     takeover without a human reply within 1 business hour -> wa_sla_missed
  attended_check             BOOKED, meeting ended > 2 h ago -> the meeting_ended rule (hand off)
  decay_scores               score.decay (halve after 30 days without a human event)
  run_due_jobs               jobs.run_due (background LLM jobs left over by requests)
  retry_alerts               re-post unposted alerts; hourly digest of integration errors
  check_mailboxes            daily bounce-rate check (health.check_mailboxes)

Running the tick twice at the same `now` sends and alerts nothing twice: every send / alert is
guarded by a timestamp column or a variant flag written in the same transaction (WhatsApp
templates: written and committed before the send). Only one worker runs a tick at a time (a
Postgres advisory lock, or a lease row in `worker_lock` elsewhere); a second one skips.

Run it inside the web app (RUN_WORKER=1, every WORKER_INTERVAL_S seconds) or on its own:
`python -m outreach.worker --once` / `--loop`. Logs carry lead / event / job ids and exception
type names only.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import Select, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import jobs
from .actions import Alert, Deps, execute, record_integration_error
from .botfilter import Scanners, classify_event, load_scanners
from .chatbot import takeover_active
from .config import Settings
from .health import check_mailboxes
from .holds import INBOUND_KINDS, _restart, release_eu_holds, resume_account_holds
from .hours import business_deadline, business_zone, parse_business_hours
from .models import Alert as AlertRow
from .models import ChatMessage, Event, Lead, WorkerLock, WorkerMark
from .outreach import (
    FAILED_SUFFIX, INTRO_ATTEMPT, INTRO_TEMPLATE, REMINDER_ATTEMPT, REMINDER_TEMPLATE, send_template_once,
    set_flag, start_outreach, switch_channel,
)
from .rules import EventSnapshot, LeadSnapshot, decide
from .score import DECAY_AFTER, decay
from .sequencer import END_WAIT_DAYS, SCHEDULE_DAYS, end_of_sequence_route
from .states import TERMINAL, LeadState

__all__ = ["tick", "main", "parse_business_hours", "business_zone", "business_deadline"]

logger = logging.getLogger(__name__)

S = LeadState
SessionFactory = Callable[[], Session]
NowFn = Callable[[], datetime]

DEFAULT_BATCH = 50
SCAN_CHUNK = 200
CLICK_SETTLE = timedelta(seconds=30)
SIBLING_WINDOW = timedelta(seconds=10)
BOOKING_NUDGE_AFTER = timedelta(minutes=30)
BOOKING_NUDGE_LOOKBACK = timedelta(days=7)
WA_REMINDER_AFTER = timedelta(hours=48)
WA_NO_RESPONSE_AFTER = timedelta(hours=48)
WA_UNCONFIRMED_AFTER = timedelta(hours=1)
WA_STATUS_SKEW = timedelta(minutes=5)  # Meta's status time vs our clock
WA_STATUS_KINDS = ("wa_sent", "wa_delivered", "wa_read")
END_WAIT = timedelta(days=END_WAIT_DAYS)
LAST_STEP = max(SCHEDULE_DAYS)
SLA = timedelta(hours=1)
SLA_REALERT = timedelta(minutes=30)
ATTENDED_AFTER = timedelta(hours=2)
ALERT_RETRY_AFTER = timedelta(minutes=1)
ALERT_RETRY_MAX = 20
DIGEST_EVERY = timedelta(hours=1)
DIGEST_LATEST = 5
DIGEST_TEXT_MAX = 150
MAILBOX_EVERY = timedelta(hours=24)
START_MAX_ATTEMPTS = 3
START_RETRY = timedelta(minutes=10)
LOCK_NAME = "tick"
LOCK_LEASE = timedelta(minutes=15)
PG_LOCK_KEY = 0x0207_7E11  # any constant bigint, the same for every worker of this app
_TERMINAL_VALUES = tuple(s.value for s in TERMINAL)


@dataclass
class Ctx:
    session_factory: SessionFactory
    deps: Deps
    settings: Settings
    now: datetime
    now_fn: NowFn
    batch: int
    scanners: Scanners


# ====================================================================== helpers

def _ts(raw: Any) -> Optional[datetime]:
    """An ISO timestamp from variant_flags / Cal.com ("...Z" too); None if missing or bad."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_DELAY_DEFAULTS = {"click_settle": CLICK_SETTLE, "booking_nudge_after": BOOKING_NUDGE_AFTER,
                   "wa_reminder_after": WA_REMINDER_AFTER, "wa_no_response_after": WA_NO_RESPONSE_AFTER,
                   "end_of_sequence_wait": END_WAIT}


def _delay(ctx: Ctx, name: str) -> timedelta:
    """Settings.delay(name) (overrides / TIME_SCALE; defaults are the constants above), or the
    constant for a settings object without it."""
    fn = getattr(ctx.settings, "delay", None)
    return fn(name) if callable(fn) else _DELAY_DEFAULTS[name]


def _flags(lead: Lead) -> Dict[str, Any]:
    return lead.variant_flags or {}


def _pick(session: Session, stmt: Select, keep: Callable[[Any], bool], limit: int) -> List[Any]:
    """Up to `limit` rows of `stmt` (already ordered oldest first) that pass `keep` (filters SQL
    cannot express portably, e.g. on JSON flags)."""
    out: List[Any] = []
    offset = 0
    while len(out) < limit:
        rows = session.scalars(stmt.limit(SCAN_CHUNK).offset(offset)).all()
        if not rows:
            break
        for row in rows:
            if keep(row):
                out.append(row)
                if len(out) >= limit:
                    break
        offset += SCAN_CHUNK
    return out


def _record(session: Session, lead_id: Optional[int], what: str, now: datetime) -> None:
    try:
        record_integration_error(session, lead_id, what, now)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - recording must never raise
        session.rollback()
        logger.error("worker: integration_error not recorded: %s", type(exc).__name__)


def _each(ctx: Ctx, session: Session, name: str, rows: Iterable[Any], fn: Callable[[Any], bool],
          lead_id: Callable[[Any], Optional[int]] = lambda r: r.id) -> int:
    """fn(row) per row, each committed on its own; a failure is rolled back, logged and recorded,
    and the next row goes on. Returns how many rows fn reported as handled."""
    n = 0
    for row in rows:
        rid = lead_id(row)
        try:
            if fn(row):
                n += 1
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one bad lead must not stop the others
            session.rollback()
            logger.error("worker %s: lead %s failed: %s", name, rid, type(exc).__name__)
            _record(session, rid, f"lead {rid}: worker {name} failed: {type(exc).__name__}", ctx.now)
    return n


def _no_inbound_since(session: Session, lead: Lead, since: Optional[datetime]) -> bool:
    if since is None:
        return False
    return session.scalar(select(Event.id).where(
        Event.lead_id == lead.id, Event.kind.in_(INBOUND_KINDS), Event.occurred_at >= since).limit(1)) is None


def _mark(session: Session, name: str) -> Optional[datetime]:
    row = session.get(WorkerMark, name)
    return row.at if row is not None else None


def _set_mark(session: Session, name: str, at: datetime) -> None:
    row = session.get(WorkerMark, name)
    if row is None:
        session.add(WorkerMark(name=name, at=at))
    else:
        row.at = at
    session.flush()


def _synthetic(kind: str, now: datetime, **data: Any) -> EventSnapshot:
    return EventSnapshot(kind=kind, channel="system", tag=None, verdict="human", step=None, occurred_at=now,
                         data=data)


# ====================================================================== 1. start_new

def _start_failed(ctx: Ctx, session: Session, lead: Lead) -> None:
    attempts = int(_flags(lead).get("start_attempts", 0)) + 1
    set_flag(lead, "start_attempts", attempts)
    if attempts < START_MAX_ATTEMPTS:
        set_flag(lead, "start_retry_at", (ctx.now + START_RETRY).isoformat())
        return
    set_flag(lead, "start_failed", ctx.now.isoformat())
    logger.error("lead %s: outreach could not start after %s attempts", lead.id, attempts)
    execute(session, lead, [Alert("unsure", f"lead {lead.id}: outreach could not start after {attempts} "
                                            "attempts (email tool / campaign settings?); please check")],
            ctx.deps, ctx.now)


def _start_new(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel.is_not(None), Lead.tool_lead_id.is_(None),
        Lead.wa_template_sent_at.is_(None),
        or_(Lead.active_channel != "email", Lead.email_hold.is_(None)))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        f = _flags(lead)
        if lead.active_channel == "whatsapp" and f.get(INTRO_ATTEMPT):
            return False  # attempted: never resent (wa_unconfirmed reconciles it)
        if f.get("start_failed"):
            return False
        retry = _ts(f.get("start_retry_at"))
        return retry is None or retry <= ctx.now

    def one(lead: Lead) -> bool:
        result = start_outreach(session, lead, ctx.deps, ctx.now, settings=ctx.settings, before_send=session.commit)
        if lead.active_channel == "email" and lead.tool_lead_id is None and result in ("error", "started"):
            _start_failed(ctx, session, lead)
        logger.info("lead %s: start %s", lead.id, result)
        return True

    return _each(ctx, session, "start_new", _pick(session, stmt, keep, ctx.batch), one)


# ====================================================================== 2. settled clicks

def _sent_at(session: Session, ev: Event, lead: Optional[Lead]) -> Optional[datetime]:
    """When the message the click came from was sent (email: that step's `sent` event)."""
    if lead is None or ev.channel != "email" or ev.step is None:
        return None
    sent = session.scalar(select(func.max(Event.occurred_at)).where(
        Event.lead_id == lead.id, Event.kind == "sent", Event.channel == "email", Event.step == ev.step,
        Event.occurred_at <= ev.occurred_at))
    if sent is None and ev.step == lead.step_sent:
        sent = lead.last_sent_at
    return sent


def _siblings(session: Session, ev: Event) -> List[Event]:
    if ev.lead_id is None:
        return []
    step_clause = Event.step.is_(None) if ev.step is None else Event.step == ev.step
    return list(session.scalars(select(Event).where(
        Event.kind == "click", Event.lead_id == ev.lead_id, Event.channel == ev.channel, step_clause,
        Event.id != ev.id, Event.occurred_at >= ev.occurred_at - SIBLING_WINDOW,
        Event.occurred_at <= ev.occurred_at + SIBLING_WINDOW)))


def _process_settled_clicks(ctx: Ctx, session: Session) -> int:
    rows = list(session.scalars(select(Event).where(
        Event.kind == "click", Event.processed.is_(False), Event.received_at <= ctx.now - _delay(ctx, "click_settle"))
        .order_by(Event.received_at, Event.id).limit(ctx.batch)))

    def one(ev: Event) -> bool:
        lead = session.get(Lead, ev.lead_id) if ev.lead_id is not None else None
        ev.verdict = classify_event(ev, _sent_at(session, ev, lead), _siblings(session, ev), ctx.scanners)
        if lead is not None and ev.tag != "unsubscribe":
            actions = decide(LeadSnapshot.from_lead(lead, ctx.now), EventSnapshot.from_event(ev), ctx.now)
            execute(session, lead, actions, ctx.deps, ctx.now, event=ev)
        ev.processed = True
        logger.info("click event %s (lead %s, %s): %s", ev.id, ev.lead_id, ev.tag, ev.verdict)
        return True

    return _each(ctx, session, "process_settled_clicks", rows, one, lambda ev: ev.lead_id)


# ====================================================================== 3. booking click, not booked

def _booking_click_not_booked(ctx: Ctx, session: Session) -> int:
    stmt = (select(Event).where(
        Event.kind == "click", Event.tag == "booking", Event.verdict == "human", Event.processed.is_(True),
        Event.lead_id.is_not(None), Event.occurred_at <= ctx.now - _delay(ctx, "booking_nudge_after"),
        Event.occurred_at >= ctx.now - BOOKING_NUDGE_LOOKBACK)
        .order_by(Event.occurred_at, Event.id))
    seen: set = set()

    def keep(ev: Event) -> bool:
        if ev.lead_id in seen:
            return False
        seen.add(ev.lead_id)
        lead = session.get(Lead, ev.lead_id)
        return lead is not None and not _flags(lead).get("booking_nudge_at")

    def one(ev: Event) -> bool:
        lead = session.get(Lead, ev.lead_id)
        booked = session.scalar(select(Event.id).where(
            Event.lead_id == lead.id, Event.kind == "booking_created", Event.occurred_at >= ev.occurred_at)
            .limit(1)) is not None
        set_flag(lead, "booking_nudge_at", ctx.now.isoformat())
        session.commit()  # once per lead, even if the send below dies half way
        if booked:
            return False
        actions = decide(LeadSnapshot.from_lead(lead, ctx.now),
                         _synthetic("booking_click_not_booked", ctx.now, click_event_id=ev.id), ctx.now)
        execute(session, lead, actions, ctx.deps, ctx.now)
        logger.info("lead %s: booking click not booked, %s action(s)", lead.id, len(actions))
        return bool(actions)

    return _each(ctx, session, "booking_click_not_booked", _pick(session, stmt, keep, ctx.batch), one,
                 lambda ev: ev.lead_id)


# ====================================================================== 4-5. WhatsApp

_TEMPLATE_SENDS = ((INTRO_ATTEMPT, "wa_template_sent_at", INTRO_TEMPLATE),
                   (REMINDER_ATTEMPT, "wa_reminder_sent_at", REMINDER_TEMPLATE))


def _wa_unconfirmed(ctx: Ctx, session: Session) -> int:
    """A template send that ended in an unknown error was never resent. Confirm it from Meta's status
    webhooks; with no status after 1 h, ask a person (once)."""
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        or_(Lead.wa_template_sent_at.is_(None), Lead.wa_reminder_sent_at.is_(None)))
        .order_by(Lead.created_at, Lead.id))

    def pending(lead: Lead) -> List[Tuple[str, str, str, datetime]]:
        out = []
        f = _flags(lead)
        for key, attr, name in _TEMPLATE_SENDS:
            at = _ts(f.get(key))
            if (at is not None and getattr(lead, attr) is None and not f.get(key + FAILED_SUFFIX)
                    and not f.get(key + "_unconfirmed")):
                out.append((key, attr, name, at))
        return out

    def one(lead: Lead) -> bool:
        done = False
        for key, attr, name, at in pending(lead):
            status = session.scalar(select(func.min(Event.occurred_at)).where(
                Event.lead_id == lead.id, Event.kind.in_(WA_STATUS_KINDS), Event.occurred_at >= at - WA_STATUS_SKEW))
            if status is not None:
                setattr(lead, attr, status)
                logger.info("lead %s: WhatsApp %s confirmed by status webhook", lead.id, name)
                done = True
            elif ctx.now - at >= WA_UNCONFIRMED_AFTER:
                set_flag(lead, key + "_unconfirmed", ctx.now.isoformat())
                execute(session, lead, [Alert("wa_send_failed", (
                    f"lead {lead.id}: WhatsApp template {name} may not have been sent (no delivery status "
                    "1 h after an unclear send error). It was NOT resent; please check the chat."))],
                    ctx.deps, ctx.now)
                logger.warning("lead %s: WhatsApp %s unconfirmed after 1 h", lead.id, name)
                done = True
        return done

    return _each(ctx, session, "wa_unconfirmed", _pick(session, stmt, lambda ld: bool(pending(ld)), ctx.batch), one)


def _wa_reminder(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        Lead.wa_template_sent_at <= ctx.now - _delay(ctx, "wa_reminder_after"), Lead.wa_reminder_sent_at.is_(None))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        f = _flags(lead)
        return (not f.get("wa_reminder_cancelled") and not f.get(REMINDER_ATTEMPT)
                and _no_inbound_since(session, lead, lead.wa_template_sent_at))

    def one(lead: Lead) -> bool:
        result = send_template_once(session, lead, REMINDER_TEMPLATE, REMINDER_ATTEMPT, "wa_reminder_sent_at",
                                    ctx.deps, ctx.now, before_send=session.commit)
        logger.info("lead %s: WhatsApp reminder %s", lead.id, result)
        return result == "sent"

    return _each(ctx, session, "wa_reminder", _pick(session, stmt, keep, ctx.batch), one)


def _wa_no_response(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        Lead.wa_reminder_sent_at <= ctx.now - _delay(ctx, "wa_no_response_after"))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        return _no_inbound_since(session, lead, lead.wa_template_sent_at or lead.wa_reminder_sent_at)

    def one(lead: Lead) -> bool:
        target = switch_channel(session, lead, "wa_no_response", ctx.deps, ctx.now, settings=ctx.settings)
        logger.info("lead %s: no WhatsApp response, switched to %s", lead.id, target or "nurture")
        return True

    return _each(ctx, session, "wa_no_response", _pick(session, stmt, keep, ctx.batch), one)


# ====================================================================== 6. holds

def _resume_paused(ctx: Ctx, session: Session) -> int:
    leads = list(session.scalars(select(Lead).where(
        Lead.state == S.PAUSED.value, Lead.pause_reason == "ooo", Lead.pause_until.is_not(None),
        Lead.pause_until <= ctx.now).order_by(Lead.pause_until, Lead.id).limit(ctx.batch)))

    def one(lead: Lead) -> bool:
        _restart(session, lead, ctx.deps.tool, ctx.now, "ooo_resume")
        lead.pause_until = None
        logger.info("lead %s: out-of-office over, back in sequence", lead.id)
        return True

    return _each(ctx, session, "resume_paused", leads, one)


def _account_holds(ctx: Ctx, session: Session) -> int:
    return resume_account_holds(session, ctx.deps.tool, ctx.now)


def _eu_holds(ctx: Ctx, session: Session) -> int:
    if not ctx.settings.eu_email_approved:
        return 0
    return release_eu_holds(session, ctx.deps.tool, ctx.now)


# ====================================================================== 7. end of sequence

def _end_of_sequence(ctx: Ctx, session: Session) -> int:
    leads = list(session.scalars(select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "email", Lead.step_sent >= LAST_STEP,
        Lead.last_sent_at <= ctx.now - _delay(ctx, "end_of_sequence_wait")).order_by(Lead.last_sent_at, Lead.id).limit(ctx.batch)))

    def one(lead: Lead) -> bool:
        clicks = session.scalar(select(func.count(Event.id)).where(
            Event.lead_id == lead.id, Event.kind == "click", Event.verdict == "human")) or 0
        route, actions = end_of_sequence_route(lead, int(clicks))
        lead.nurture_track = route
        execute(session, lead, actions, ctx.deps, ctx.now)
        logger.info("lead %s: sequence ended, route %s", lead.id, route)
        return True

    return _each(ctx, session, "end_of_sequence", leads, one)


# ====================================================================== 8. WhatsApp human SLA

def _sla_pending_since(session: Session, lead: Lead, takeover_at: datetime) -> Optional[datetime]:
    """Since when the lead has waited for a person: the takeover, or the first lead message after the
    last human reply. None = nobody is waiting."""
    last_human = session.scalar(select(func.max(ChatMessage.created_at)).where(
        ChatMessage.lead_id == lead.id, ChatMessage.direction == "out", ChatMessage.author == "human",
        ChatMessage.created_at >= takeover_at))
    if last_human is None:
        return takeover_at
    return session.scalar(select(func.min(ChatMessage.created_at)).where(
        ChatMessage.lead_id == lead.id, ChatMessage.direction == "in", ChatMessage.created_at > last_human))


def _wa_sla(ctx: Ctx, session: Session) -> int:
    hours = parse_business_hours(ctx.settings.business_hours)
    tz = business_zone(ctx.settings.business_tz)
    stmt = (select(Lead).where(
        Lead.state.not_in(_TERMINAL_VALUES), Lead.phone.is_not(None),
        Lead.variant_flags["takeover_at"].as_string().is_not(None))
        .order_by(Lead.created_at, Lead.id))

    def due(lead: Lead) -> bool:
        if not takeover_active(lead):
            return False
        f = _flags(lead)
        takeover_at = _ts(f.get("takeover_at"))
        if takeover_at is None:
            return False
        since = _sla_pending_since(session, lead, takeover_at)
        if since is None or ctx.now < business_deadline(since, SLA, hours, tz):
            return False
        last = f.get("wa_sla") if isinstance(f.get("wa_sla"), dict) else None
        if last is None or last.get("takeover") != f.get("takeover_at"):
            return True  # first alert for this takeover
        last_at = _ts(last.get("at"))
        if last_at is None:
            return True
        if ctx.now - last_at < SLA_REALERT:
            return False
        newest_in = session.scalar(select(func.max(ChatMessage.created_at)).where(
            ChatMessage.lead_id == lead.id, ChatMessage.direction == "in"))
        return newest_in is not None and newest_in > last_at  # the lead wrote again since

    def one(lead: Lead) -> bool:
        f = _flags(lead)
        again = isinstance(f.get("wa_sla"), dict) and f["wa_sla"].get("takeover") == f.get("takeover_at")
        set_flag(lead, "wa_sla", {"takeover": f.get("takeover_at"), "at": ctx.now.isoformat()})
        what = "is still waiting and wrote again" if again else "has had no reply from a person"
        execute(session, lead, [Alert("wa_sla_missed", (
            f"lead {lead.id}: WhatsApp chat {what} (SLA: 1 business hour). "
            f"Reply: wa {lead.id} your message"))], ctx.deps, ctx.now)
        logger.warning("lead %s: WhatsApp SLA missed%s", lead.id, " (again)" if again else "")
        return True

    return _each(ctx, session, "wa_sla", _pick(session, stmt, due, ctx.batch), one)


# ====================================================================== 9. attended check

def _attended_check(ctx: Ctx, session: Session) -> int:
    """BOOKED with the meeting over > 2 h and no MEETING_ENDED webhook: hand off as the meeting_ended
    rule would. A no-show moves the lead out of BOOKED (rules), so BOOKED means none was recorded."""
    stmt = select(Lead).where(Lead.state == S.BOOKED.value).order_by(Lead.created_at, Lead.id)

    def keep(lead: Lead) -> bool:
        end = _ts(_flags(lead).get("booking_end"))
        return end is not None and ctx.now - end > ATTENDED_AFTER

    def one(lead: Lead) -> bool:
        uid = _flags(lead).get("booking_uid")
        row = Event(lead_id=lead.id, channel="system", kind="meeting_ended", occurred_at=ctx.now,
                    received_at=ctx.now, verdict="n/a", processed=True,
                    data={"uid": uid, "source": "attended_check"} if uid else {"source": "attended_check"})
        session.add(row)
        session.flush()
        actions = decide(LeadSnapshot.from_lead(lead, ctx.now), EventSnapshot.from_event(row), ctx.now)
        execute(session, lead, actions, ctx.deps, ctx.now, event=row)
        logger.info("lead %s: meeting over without MEETING_ENDED, handed off", lead.id)
        return bool(actions)

    return _each(ctx, session, "attended_check", _pick(session, stmt, keep, ctx.batch), one)


# ====================================================================== 10. decay

def _decay_scores(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.score > 0, Lead.state.not_in(_TERMINAL_VALUES),
        func.coalesce(Lead.last_human_event_at, Lead.created_at) <= ctx.now - DECAY_AFTER)
        .order_by(Lead.created_at, Lead.id))
    changed = _pick(session, stmt, lambda lead: decay(lead, ctx.now), ctx.batch)  # decay() applies it
    if changed:
        logger.info("worker: %s score(s) decayed", len(changed))
    return len(changed)


# ====================================================================== 11. background jobs

def _run_due_jobs(ctx: Ctx, session: Session) -> int:
    return jobs.run_due(ctx.session_factory, ctx.deps, ctx.now_fn)


# ====================================================================== 12. alerts

def _retry_alerts(ctx: Ctx, session: Session) -> int:
    alerter = ctx.deps.alerter
    repost = getattr(alerter, "repost", None)
    n = 0
    if repost is not None:
        rows = list(session.scalars(select(AlertRow).where(
            AlertRow.posted_at.is_(None), AlertRow.kind != "integration_error",
            AlertRow.created_at <= ctx.now - ALERT_RETRY_AFTER)
            .order_by(AlertRow.created_at, AlertRow.id).limit(ALERT_RETRY_MAX)))

        def one(row: AlertRow) -> bool:
            if not repost(session, row, ctx.now):
                return False
            row.posted_at = ctx.now
            return True

        n = _each(ctx, session, "retry_alerts", rows, one, lambda r: r.lead_id)
    return n + _integration_digest(ctx, session)


def _integration_digest(ctx: Ctx, session: Session) -> int:
    """Integration errors are not posted one by one (Teams may be what is failing): one digest an hour."""
    post_text = getattr(ctx.deps.alerter, "post_text", None)
    if post_text is None:
        return 0
    last = _mark(session, "integration_digest")
    if last is not None and ctx.now - last < DIGEST_EVERY:
        return 0
    base = select(AlertRow).where(AlertRow.posted_at.is_(None), AlertRow.kind == "integration_error",
                                  AlertRow.created_at <= ctx.now)
    count = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    if not count:
        return 0
    max_id = session.scalar(select(func.max(AlertRow.id)).where(
        AlertRow.posted_at.is_(None), AlertRow.kind == "integration_error", AlertRow.created_at <= ctx.now))
    latest = list(session.scalars(base.order_by(AlertRow.created_at.desc(), AlertRow.id.desc()).limit(DIGEST_LATEST)))
    lines = [f"{count} integration error(s) since the last digest; latest {len(latest)}:"]
    lines += [f"{r.kind} (lead {r.lead_id if r.lead_id is not None else 'none'}): {(r.text or '')[:DIGEST_TEXT_MAX]}"
              for r in latest]
    _set_mark(session, "integration_digest", ctx.now)
    session.commit()  # one attempt an hour, even if Teams is down
    if not post_text("Integration errors (hourly digest)", lines, ctx.now):
        logger.warning("worker: integration digest not posted (%s error(s) pending)", count)
        return 0
    session.execute(update(AlertRow).where(
        AlertRow.posted_at.is_(None), AlertRow.kind == "integration_error", AlertRow.id <= max_id)
        .values(posted_at=ctx.now).execution_options(synchronize_session=False))
    logger.info("worker: integration digest posted (%s error(s))", count)
    return 1


# ====================================================================== 13. mailboxes

def _check_mailboxes(ctx: Ctx, session: Session) -> int:
    last = _mark(session, "check_mailboxes")
    if last is not None and ctx.now - last < MAILBOX_EVERY:
        return 0
    _set_mark(session, "check_mailboxes", ctx.now)
    flagged = check_mailboxes(session, ctx.deps.tool, ctx.deps.alerter, ctx.now)
    return len(flagged)


JobFn = Callable[[Ctx, Session], int]

JOBS: Sequence[Tuple[str, JobFn]] = (
    ("start_new", _start_new),
    ("process_settled_clicks", _process_settled_clicks),
    ("booking_click_not_booked", _booking_click_not_booked),
    ("wa_unconfirmed", _wa_unconfirmed),
    ("wa_reminder", _wa_reminder),
    ("wa_no_response", _wa_no_response),
    ("resume_paused", _resume_paused),
    ("account_holds", _account_holds),
    ("eu_holds", _eu_holds),
    ("end_of_sequence", _end_of_sequence),
    ("wa_sla", _wa_sla),
    ("attended_check", _attended_check),
    ("decay_scores", _decay_scores),
    ("run_due_jobs", _run_due_jobs),
    ("retry_alerts", _retry_alerts),
    ("check_mailboxes", _check_mailboxes),
)


# ====================================================================== lock

class _Lock:
    def __init__(self, release: Callable[[], None], renew: Callable[[], None]) -> None:
        self.release, self.renew = release, renew


def _acquire(session_factory: SessionFactory, now_fn: NowFn) -> Optional[_Lock]:
    s = session_factory()
    bind = s.get_bind()
    if bind.dialect.name == "postgresql":
        s.close()
        conn = bind.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": PG_LOCK_KEY}).scalar()
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return None

        def pg_release() -> None:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PG_LOCK_KEY})
            finally:
                conn.close()

        return _Lock(pg_release, lambda: None)

    owner = uuid.uuid4().hex
    try:
        now = now_fn()
        res = s.execute(update(WorkerLock).where(
            WorkerLock.name == LOCK_NAME, or_(WorkerLock.expires_at < now, WorkerLock.owner == owner))
            .values(owner=owner, expires_at=now + LOCK_LEASE).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            if s.get(WorkerLock, LOCK_NAME) is not None:
                s.rollback()
                return None
            try:
                with s.begin_nested():
                    s.add(WorkerLock(name=LOCK_NAME, owner=owner, expires_at=now + LOCK_LEASE))
            except IntegrityError:
                s.rollback()
                return None
        s.commit()
    finally:
        s.close()

    def _write(stmt: Any) -> None:
        ls = session_factory()
        try:
            ls.execute(stmt.execution_options(synchronize_session=False))
            ls.commit()
        except Exception as exc:  # noqa: BLE001 - worst case the lease simply expires
            ls.rollback()
            logger.warning("worker lock update failed: %s", type(exc).__name__)
        finally:
            ls.close()

    def release() -> None:
        _write(delete(WorkerLock).where(WorkerLock.name == LOCK_NAME, WorkerLock.owner == owner))

    def renew() -> None:
        _write(update(WorkerLock).where(WorkerLock.name == LOCK_NAME, WorkerLock.owner == owner)
               .values(expires_at=now_fn() + LOCK_LEASE))

    return _Lock(release, renew)


# ====================================================================== tick

@lru_cache(maxsize=1)
def _default_scanners() -> Scanners:
    return load_scanners()


def _run(ctx: Ctx, name: str, fn: JobFn) -> int:
    s = ctx.session_factory()
    try:
        n = fn(ctx, s)
        s.commit()
        return int(n or 0)
    except Exception as exc:  # noqa: BLE001 - a failing job never stops the others
        s.rollback()
        logger.error("worker job %s failed: %s", name, type(exc).__name__)
        _record(s, None, f"worker job {name} failed: {type(exc).__name__}", ctx.now)
        return -1
    finally:
        s.close()


def tick(session_factory: SessionFactory, deps: Deps, now_fn: NowFn, settings: Settings, *,
         batch: Optional[int] = None, scanners: Optional[Scanners] = None) -> Dict[str, int]:
    """Run every job once. Returns {job name: leads/items handled (-1 = the job failed),
    "failed_jobs": n}, or {"skipped": 1} when another worker holds the lock."""
    try:
        lock = _acquire(session_factory, now_fn)
    except Exception as exc:  # noqa: BLE001 - e.g. the database is down: try again next tick
        logger.error("worker tick: lock failed: %s", type(exc).__name__)
        return {"skipped": 1}
    if lock is None:
        logger.info("worker tick skipped: another worker is running")
        return {"skipped": 1}
    try:
        ctx = Ctx(session_factory=session_factory, deps=deps, settings=settings, now=now_fn(), now_fn=now_fn,
                  batch=batch or getattr(settings, "worker_batch_size", DEFAULT_BATCH),
                  scanners=scanners or _default_scanners())
        results: Dict[str, int] = {}
        failed = 0
        for name, fn in JOBS:
            results[name] = n = _run(ctx, name, fn)
            if n < 0:
                failed += 1
            lock.renew()
        results["failed_jobs"] = failed
        busy = {k: v for k, v in results.items() if v}
        logger.info("worker tick done: %s", busy or "nothing to do")
        return results
    finally:
        lock.release()


# ====================================================================== CLI

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def main(argv: Optional[List[str]] = None, *, settings: Optional[Settings] = None, deps: Optional[Deps] = None,
         engine: Any = None, now_fn: NowFn = _utcnow) -> int:
    """`python -m outreach.worker --once` (one tick) or `--loop` (every WORKER_INTERVAL_S seconds until
    SIGINT / SIGTERM). Uses DATABASE_URL and the same settings as the web app."""
    parser = argparse.ArgumentParser(prog="python -m outreach.worker", description="Run the outreach worker tick.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one tick and exit")
    mode.add_argument("--loop", action="store_true", help="tick every WORKER_INTERVAL_S seconds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from .app import wire_deps  # late import: app imports this module's tick lazily too
    from .config import get_settings
    from .db import init_db, make_engine

    settings = settings or get_settings()
    if engine is None:
        engine = make_engine(settings.database_url)
    init_db(engine)
    if now_fn is _utcnow:
        from .localclock import default_clock

        now_fn = default_clock(settings)  # the fake clock only when APP_ENV=local
    if deps is None:
        from .app import default_deps
        from .verify import make_verifier

        deps = default_deps(settings)
        wire_deps(deps, settings, make_verifier(settings))
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    if args.once:
        tick(factory, deps, now_fn, settings)
        return 0

    stop = threading.Event()

    def _stop(signum: int, _frame: Any) -> None:
        logger.info("worker: signal %s, stopping after this tick", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except ValueError:  # pragma: no cover - not the main thread
            pass
    logger.info("worker loop started (every %ss)", settings.worker_interval_s)
    while not stop.is_set():
        try:
            tick(factory, deps, now_fn, settings)
        except Exception as exc:  # noqa: BLE001 - keep looping
            logger.error("worker tick crashed: %s", type(exc).__name__)
        stop.wait(settings.worker_interval_s)
    logger.info("worker loop stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
