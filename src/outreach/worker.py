"""The worker tick: every time-driven step of the outreach flow (plan Task 12).

tick() runs the jobs below in order, each in its own session and transaction. A failing job is
logged (exception type only), recorded as an integration_error and never stops the jobs after it.
Jobs that touch leads handle at most `batch` leads per tick (default 50), oldest first, and commit
per lead, so one bad lead does not hold up the rest.

  start_new                  IN_SEQUENCE leads not started yet -> outreach.start_outreach
                             (3 failed email starts -> alert + NURTURE "start_failed")
  process_settled_clicks     clicks older than 30 s: bot filter verdict, then decide/execute (points)
  booking_click_not_booked   30 min after a human booking click with no booking: slots reply, once
  wa_unconfirmed             template sends with an unknown outcome: confirm from status webhooks;
                             alert after 1 h, give up after 24 h (email fallback, else NURTURE)
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
  retry_tool_stops           failed email-tool stop / block-list calls, retried 5 min, 30 min, 2 h, then 6-hourly
  retry_verification         leads held as verify_error: re-verify 15 min, 1 h, 6 h, 24 h after intake, then
                             one verify_failed alert (the lead stays held)
  stuck_leads                safety net: IN_SEQUENCE with nothing sent 3 days after the expected first
                             send -> one stuck_lead alert per lead

Every lead job re-loads its lead with a row lock (FOR UPDATE on Postgres, populate_existing) right
before acting on it and re-checks the job's own selection predicate on that fresh row: a lead that
changed since it was picked (a reply, a booking, a flag) is skipped, and nothing is written from a
stale copy.

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

from sqlalchemy import Select, and_, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import jobs
from .actions import (
    TOOL_BLOCK_PENDING, TOOL_REALERT_AFTER, TOOL_STOP_PENDING, Alert, Deps, SetState, execute,
    record_integration_error, tool_retry_delay, tool_stop_failed_text,
)
from .botfilter import TRAP_TAG, Scanners, classify_event, load_scanners
from .chatbot import takeover_active
from .config import DELAY_DEFAULTS_S, Settings
from .health import check_mailboxes
from .holds import INBOUND_KINDS, _restart, release_eu_holds, resume_account_holds
from .intake import EU_HOLD, VERIFY_HOLD
from .channels import EMAIL_OK_STATUSES
from .verify import VERIFY_ERROR
from .hours import business_deadline, business_zone, parse_business_hours
from .models import Alert as AlertRow
from .models import AlertAttempt, ChatMessage, Event, Lead, WorkerLock, WorkerMark
from .outreach import (
    FAILED_SUFFIX, INTRO_ATTEMPT, INTRO_TEMPLATE, REMINDER_ATTEMPT, REMINDER_TEMPLATE, WA_RETRY_AFTER,
    handle_wa_send_failure, send_template_once, set_flag, start_outreach, switch_channel, wa_gave_up,
)
from .rules import EventSnapshot, LeadSnapshot, decide
from .score import DECAY_AFTER, decay
from .sequencer import LAST_STEP, end_of_sequence_route, enroll
from .states import TERMINAL_VALUES, LeadState

__all__ = ["tick", "main", "parse_business_hours", "business_zone", "business_deadline"]

logger = logging.getLogger(__name__)

S = LeadState
SessionFactory = Callable[[], Session]
NowFn = Callable[[], datetime]

DEFAULT_BATCH = 50
SCAN_CHUNK = 200
CLICK_SETTLE = timedelta(seconds=DELAY_DEFAULTS_S["click_settle"])
CLICK_MAX_FAILURES = 3
# other clicks on the same email that can change a verdict: the multi-link burst (10 s, checked in
# botfilter) and a honeypot hit from the same network (botfilter.TRAP_WINDOW)
SIBLING_WINDOW = timedelta(hours=1)
# a click from our click page waits at least this long, so the browser's beacon is in before the
# verdict (matters only when TIME_SCALE shrinks click_settle below it)
JS_CONFIRM_GRACE = timedelta(seconds=10)
BOOKING_NUDGE_AFTER = timedelta(seconds=DELAY_DEFAULTS_S["booking_nudge_after"])
BOOKING_NUDGE_LOOKBACK = timedelta(days=7)
WA_REMINDER_AFTER = timedelta(seconds=DELAY_DEFAULTS_S["wa_reminder_after"])
WA_NO_RESPONSE_AFTER = timedelta(seconds=DELAY_DEFAULTS_S["wa_no_response_after"])
WA_UNCONFIRMED_AFTER = timedelta(hours=1)
WA_UNCONFIRMED_GIVE_UP = timedelta(hours=24)  # after the unconfirmed alert
STUCK_AFTER = timedelta(days=3)
WA_STATUS_SKEW = timedelta(minutes=5)  # Meta's status time vs our clock
WA_STATUS_KINDS = ("wa_sent", "wa_delivered", "wa_read")
END_WAIT = timedelta(seconds=DELAY_DEFAULTS_S["end_of_sequence_wait"])
SLA = timedelta(hours=1)
SLA_REALERT = timedelta(minutes=30)
ATTENDED_AFTER = timedelta(hours=2)
ALERT_RETRY_AFTER = timedelta(minutes=1)
ALERT_RETRY_MAX = 20
ALERT_REPOST_MAX = 5  # tries per alert, then it is given up and listed in the digest
DIGEST_EVERY = timedelta(hours=1)
DIGEST_LATEST = 5
DIGEST_TEXT_MAX = 150
MAILBOX_EVERY = timedelta(hours=24)
START_MAX_ATTEMPTS = 3
START_RETRY = timedelta(minutes=10)
LOCK_NAME = "tick"
LOCK_LEASE = timedelta(minutes=15)
PG_LOCK_KEY = 0x0207_7E11  # any constant bigint, the same for every worker of this app
_TERMINAL_VALUES = TERMINAL_VALUES  # states.TERMINAL as strings


@dataclass
class Ctx:
    session_factory: SessionFactory
    deps: Deps
    settings: Settings
    now: datetime
    now_fn: NowFn
    batch: int
    scanners: Scanners
    renew: Callable[[], None] = lambda: None  # extends the worker lock lease


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


def _delay(ctx: Ctx, name: str) -> timedelta:
    """Settings.delay(name): config.DELAY_DEFAULTS_S (the single source of the spec delays), after
    any override / TIME_SCALE."""
    return ctx.settings.delay(name)


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
        ctx.renew()  # a long batch never outlives the lock lease
    return n


def _fresh(session: Session, lead_id: int, qualifies: Callable[[Lead], bool]) -> Optional[Lead]:
    """The lead re-read from the database with a row lock (FOR UPDATE on Postgres; SQLite serialises
    writers), replacing any stale copy in the session; None if it no longer qualifies for the job."""
    lead = session.scalars(select(Lead).where(Lead.id == lead_id).with_for_update()
                           .execution_options(populate_existing=True)).first()
    if lead is None or not qualifies(lead):
        return None
    return lead


def _each_lead(ctx: Ctx, session: Session, name: str, leads: Iterable[Lead], qualifies: Callable[[Lead], bool],
               fn: Callable[[Lead], bool]) -> int:
    """_each over leads, each re-loaded fresh and locked, and re-checked with `qualifies` first."""
    def one(lead: Lead) -> bool:
        session.commit()  # end the read that picked the batch: the lock and re-read get a fresh snapshot
        fresh = _fresh(session, lead.id, qualifies)
        if fresh is None:
            logger.info("worker %s: lead %s changed since it was picked; skipped", name, lead.id)
            return False
        return fn(fresh)

    return _each(ctx, session, name, leads, one)


def _before(at: Optional[datetime], cutoff: datetime) -> bool:
    return at is not None and at <= cutoff


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
    # never left IN_SEQUENCE with nothing going out: alert, then nurture
    execute(session, lead, [Alert("unsure", f"lead {lead.id}: outreach could not start after {attempts} "
                                            "attempts (email tool / campaign settings?); moved to nurture"),
                            SetState(S.NURTURE, "start_failed")],
            ctx.deps, ctx.now)


def _start_new(ctx: Ctx, session: Session) -> int:
    # per active channel: a lead switched WhatsApp -> email has wa_template_sent_at set and must still
    # be picked up when its email enrolment failed
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value,
        or_(and_(Lead.active_channel == "email", Lead.tool_lead_id.is_(None), Lead.email_hold.is_(None)),
            and_(Lead.active_channel == "whatsapp", Lead.wa_template_sent_at.is_(None))))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        if lead.state != S.IN_SEQUENCE.value:
            return False
        if lead.active_channel == "email":
            if lead.tool_lead_id is not None or lead.email_hold is not None:
                return False
        elif lead.active_channel != "whatsapp" or lead.wa_template_sent_at is not None:
            return False
        f = _flags(lead)
        if lead.active_channel == "whatsapp":
            if f.get(INTRO_ATTEMPT):
                return False  # attempted: never resent (wa_unconfirmed reconciles it)
            wait = _ts(f.get(WA_RETRY_AFTER))
            if wait is not None and wait > ctx.now:
                return False  # transient Meta error: backing off
        if f.get("start_failed"):
            return False
        retry = _ts(f.get("start_retry_at"))
        return retry is None or retry <= ctx.now

    def one(lead: Lead) -> bool:
        result = start_outreach(session, lead, ctx.deps, ctx.now, settings=ctx.settings, before_send=session.commit)
        if (lead.state == S.IN_SEQUENCE.value and lead.active_channel == "email" and lead.tool_lead_id is None
                and result in ("error", "started", "suppressed")):
            _start_failed(ctx, session, lead)
        logger.info("lead %s: start %s", lead.id, result)
        return True

    return _each_lead(ctx, session, "start_new", _pick(session, stmt, keep, ctx.batch), keep, one)


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
        Event.kind.in_(("click", "open")), Event.processed.is_(False),
        Event.received_at <= ctx.now - _delay(ctx, "click_settle"))
        .order_by(Event.received_at, Event.id).limit(ctx.batch)))
    rows = [ev for ev in rows if not (ev.data or {}).get("js_check")
            or _ts(ev.received_at) <= ctx.now - JS_CONFIRM_GRACE]

    def settle(ev: Event) -> None:
        lead = _fresh(session, ev.lead_id, lambda ld: True) if ev.lead_id is not None else None
        ev.verdict = classify_event(ev, _sent_at(session, ev, lead), _siblings(session, ev), ctx.scanners)
        if lead is not None and ev.tag not in ("unsubscribe", TRAP_TAG):
            snap = LeadSnapshot.from_lead(lead, ctx.now,
                                          clicks_need_check=ctx.settings.click_alerts_need_check)
            actions = decide(snap, EventSnapshot.from_event(ev), ctx.now)
            execute(session, lead, actions, ctx.deps, ctx.now, event=ev)
        ev.processed = True

    def one(ev: Event) -> bool:
        ev_id, lead_id = ev.id, ev.lead_id
        try:
            with session.begin_nested():
                settle(ev)
        except Exception as exc:  # noqa: BLE001 - counted; closed as "error" after CLICK_MAX_FAILURES
            ev = session.get(Event, ev_id)
            failures = int((ev.data or {}).get("settle_failures", 0)) + 1
            ev.data = {**(ev.data or {}), "settle_failures": failures}
            if failures >= CLICK_MAX_FAILURES:
                ev.verdict, ev.processed = "error", True
            logger.error("click event %s (lead %s): settle failed (%s of %s): %s", ev_id, lead_id, failures,
                         CLICK_MAX_FAILURES, type(exc).__name__)
            record_integration_error(session, lead_id, f"click event {ev_id}: settle failed ({failures} of "
                                                       f"{CLICK_MAX_FAILURES}): {type(exc).__name__}", ctx.now)
            return False
        logger.info("%s event %s (lead %s, %s): %s", ev.kind, ev_id, lead_id, ev.tag, ev.verdict)
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
        lead = _fresh(session, ev.lead_id, lambda ld: not _flags(ld).get("booking_nudge_at"))
        if lead is None:
            return False
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


def _status_time(session: Session, lead: Lead, template: str, attempted: datetime) -> Optional[datetime]:
    """When Meta first reported the template sent / delivered / read. By message id when the sender
    logged one for this template (exact); otherwise the first status for the lead after the attempt."""
    wamid = session.scalar(select(ChatMessage.wa_message_id).where(
        ChatMessage.lead_id == lead.id, ChatMessage.direction == "out", ChatMessage.text == f"[template:{template}]",
        ChatMessage.wa_message_id.is_not(None), ChatMessage.created_at >= attempted - WA_STATUS_SKEW)
        .order_by(ChatMessage.created_at.desc()).limit(1))
    rows = session.execute(select(Event.occurred_at, Event.data).where(
        Event.lead_id == lead.id, Event.kind.in_(WA_STATUS_KINDS), Event.occurred_at >= attempted - WA_STATUS_SKEW)
        .order_by(Event.occurred_at, Event.id)).all()
    for occurred, data in rows:
        if wamid is None or (isinstance(data, dict) and data.get("wamid") == wamid):
            return occurred
    return None


def _wa_unconfirmed(ctx: Ctx, session: Session) -> int:
    """A template send that ended in an unknown error was never resent. Confirm it from Meta's status
    webhooks; with no status after 1 h, ask a person (once)."""
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        or_(Lead.wa_template_sent_at.is_(None), Lead.wa_reminder_sent_at.is_(None)))
        .order_by(Lead.created_at, Lead.id))

    def pending(lead: Lead) -> List[Tuple[str, str, str, datetime]]:
        out = []
        if lead.state != S.IN_SEQUENCE.value or lead.active_channel != "whatsapp":
            return out
        f = _flags(lead)
        for key, attr, name in _TEMPLATE_SENDS:
            at = _ts(f.get(key))
            if at is None or getattr(lead, attr) is not None or f.get(key + FAILED_SUFFIX):
                continue
            alerted = _ts(f.get(key + "_unconfirmed"))
            if alerted is None or ctx.now - alerted >= WA_UNCONFIRMED_GIVE_UP:
                out.append((key, attr, name, at))
        return out

    def one(lead: Lead) -> bool:
        done = False
        for key, attr, name, at in pending(lead):
            status = _status_time(session, lead, name, at)
            if status is not None:
                setattr(lead, attr, status)
                logger.info("lead %s: WhatsApp %s confirmed by status webhook", lead.id, name)
                done = True
            elif _flags(lead).get(key + "_unconfirmed"):
                # still nothing 24 h after the alert: treat as failed (synthetic wa_failed, then move on)
                set_flag(lead, key + FAILED_SUFFIX, "unconfirmed")
                handle_wa_send_failure(session, lead, None, name, ctx.deps, ctx.now)
                target = wa_gave_up(session, lead, ctx.deps, ctx.now, settings=ctx.settings)
                logger.warning("lead %s: WhatsApp %s never confirmed; gave up (%s)", lead.id, name, target)
                return True
            elif ctx.now - at >= WA_UNCONFIRMED_AFTER:
                set_flag(lead, key + "_unconfirmed", ctx.now.isoformat())
                execute(session, lead, [Alert("wa_send_failed", (
                    f"lead {lead.id}: WhatsApp template {name} may not have been sent (no delivery status "
                    "1 h after an unclear send error). It was NOT resent; please check the chat."))],
                    ctx.deps, ctx.now)
                logger.warning("lead %s: WhatsApp %s unconfirmed after 1 h", lead.id, name)
                done = True
        return done

    def qualifies(lead: Lead) -> bool:
        return bool(pending(lead))

    return _each_lead(ctx, session, "wa_unconfirmed", _pick(session, stmt, qualifies, ctx.batch), qualifies, one)


def _wa_reminder(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        Lead.wa_template_sent_at <= ctx.now - _delay(ctx, "wa_reminder_after"), Lead.wa_reminder_sent_at.is_(None))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        f = _flags(lead)
        wait = _ts(f.get(WA_RETRY_AFTER))
        return (lead.state == S.IN_SEQUENCE.value and lead.active_channel == "whatsapp"
                and _before(lead.wa_template_sent_at, ctx.now - _delay(ctx, "wa_reminder_after"))
                and lead.wa_reminder_sent_at is None
                and not f.get("wa_reminder_cancelled") and not f.get(REMINDER_ATTEMPT)
                and (wait is None or wait <= ctx.now)
                and _no_inbound_since(session, lead, lead.wa_template_sent_at))

    def one(lead: Lead) -> bool:
        result = send_template_once(session, lead, REMINDER_TEMPLATE, REMINDER_ATTEMPT, "wa_reminder_sent_at",
                                    ctx.deps, ctx.now, before_send=session.commit)
        logger.info("lead %s: WhatsApp reminder %s", lead.id, result)
        if result in ("failed", "not_allowed"):
            # refused and decide() did not move the lead: WhatsApp is done, move on now rather than
            # wait for a no-response that can never come
            wa_gave_up(session, lead, ctx.deps, ctx.now, settings=ctx.settings)
        return result == "sent"

    return _each_lead(ctx, session, "wa_reminder", _pick(session, stmt, keep, ctx.batch), keep, one)


def _wa_no_response(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.state == S.IN_SEQUENCE.value, Lead.active_channel == "whatsapp",
        Lead.wa_reminder_sent_at <= ctx.now - _delay(ctx, "wa_no_response_after"))
        .order_by(Lead.created_at, Lead.id))

    def keep(lead: Lead) -> bool:
        return (lead.state == S.IN_SEQUENCE.value and lead.active_channel == "whatsapp"
                and _before(lead.wa_reminder_sent_at, ctx.now - _delay(ctx, "wa_no_response_after"))
                and _no_inbound_since(session, lead, lead.wa_template_sent_at or lead.wa_reminder_sent_at))

    def one(lead: Lead) -> bool:
        target = switch_channel(session, lead, "wa_no_response", ctx.deps, ctx.now, settings=ctx.settings)
        logger.info("lead %s: no WhatsApp response, switched to %s", lead.id, target or "nurture")
        return True

    return _each_lead(ctx, session, "wa_no_response", _pick(session, stmt, keep, ctx.batch), keep, one)


# ====================================================================== 6. holds

def _resume_paused(ctx: Ctx, session: Session) -> int:
    leads = list(session.scalars(select(Lead).where(
        Lead.state == S.PAUSED.value, Lead.pause_reason == "ooo", Lead.pause_until.is_not(None),
        Lead.pause_until <= ctx.now).order_by(Lead.pause_until, Lead.id).limit(ctx.batch)))

    def qualifies(lead: Lead) -> bool:
        return (lead.state == S.PAUSED.value and lead.pause_reason == "ooo"
                and _before(lead.pause_until, ctx.now))

    def one(lead: Lead) -> bool:
        old_tool_id = lead.tool_lead_id if lead.active_channel == "email" else None
        if old_tool_id:
            # the reply that carried the out-of-office stopped (deleted) the tool lead: nothing to resume
            lead.tool_lead_id = None
            set_flag(lead, "previous_tool_lead_id", old_tool_id)
        _restart(session, lead, ctx.deps.tool, ctx.now, "ooo_resume")
        lead.pause_until = None
        logger.info("lead %s: out-of-office over, back in sequence", lead.id)
        if old_tool_id and (lead.step_sent or 0) < LAST_STEP:
            _re_enrol(ctx, session, lead)
        return True

    return _each_lead(ctx, session, "resume_paused", leads, qualifies, one)


def _re_enrol(ctx: Ctx, session: Session, lead: Lead) -> None:
    """Re-enrol at our next unsent step (sequencer.enroll from_step). On failure the lead is left
    without a tool lead id, so start_new retries it from step 1 rather than losing it."""
    from_step = (lead.step_sent or 0) + 1
    try:
        with session.begin_nested():
            enroll(session, lead, ctx.deps.tool, ctx.now, settings=ctx.settings, from_step=from_step)
        logger.info("lead %s: re-enrolled at step %s", lead.id, from_step)
    except Exception as exc:  # noqa: BLE001 - recorded; start_new picks the lead up
        logger.warning("lead %s: re-enrol failed: %s", lead.id, type(exc).__name__)
        record_integration_error(session, lead.id, f"lead {lead.id}: re-enrol at step {from_step} failed: "
                                                   f"{type(exc).__name__}", ctx.now)


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

    def qualifies(lead: Lead) -> bool:
        return (lead.state == S.IN_SEQUENCE.value and lead.active_channel == "email"
                and (lead.step_sent or 0) >= LAST_STEP
                and _before(lead.last_sent_at, ctx.now - _delay(ctx, "end_of_sequence_wait")))

    def one(lead: Lead) -> bool:
        clicks = session.scalar(select(func.count(Event.id)).where(
            Event.lead_id == lead.id, Event.kind == "click", Event.verdict == "human")) or 0
        route, actions = end_of_sequence_route(
            lead, int(clicks), clicks_need_check=ctx.settings.click_alerts_need_check)
        lead.nurture_track = route
        execute(session, lead, actions, ctx.deps, ctx.now)
        logger.info("lead %s: sequence ended, route %s", lead.id, route)
        return True

    return _each_lead(ctx, session, "end_of_sequence", leads, qualifies, one)


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
        if lead.state in _TERMINAL_VALUES or not lead.phone or not takeover_active(lead):
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

    return _each_lead(ctx, session, "wa_sla", _pick(session, stmt, due, ctx.batch), due, one)


# ====================================================================== 9. attended check

def _attended_check(ctx: Ctx, session: Session) -> int:
    """BOOKED with the meeting over > 2 h and no MEETING_ENDED webhook: hand off as the meeting_ended
    rule would. A no-show moves the lead out of BOOKED (rules), so BOOKED means none was recorded."""
    stmt = select(Lead).where(Lead.state == S.BOOKED.value).order_by(Lead.created_at, Lead.id)

    def keep(lead: Lead) -> bool:
        end = _ts(_flags(lead).get("booking_end"))
        return lead.state == S.BOOKED.value and end is not None and ctx.now - end > ATTENDED_AFTER

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

    return _each_lead(ctx, session, "attended_check", _pick(session, stmt, keep, ctx.batch), keep, one)


# ====================================================================== 10. decay

def _decay_scores(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(
        Lead.score > 0, Lead.state.not_in(_TERMINAL_VALUES),
        func.coalesce(Lead.last_human_event_at, Lead.created_at) <= ctx.now - DECAY_AFTER)
        .order_by(Lead.created_at, Lead.id))
    def qualifies(lead: Lead) -> bool:
        last = lead.last_human_event_at or lead.created_at
        return (lead.score or 0) > 0 and lead.state not in _TERMINAL_VALUES and _before(last, ctx.now - DECAY_AFTER)

    candidates = list(session.scalars(stmt.limit(ctx.batch)))
    changed = _each_lead(ctx, session, "decay_scores", candidates, qualifies, lambda lead: decay(lead, ctx.now))
    if changed:
        logger.info("worker: %s score(s) decayed", changed)
    return changed


# ====================================================================== 11. background jobs

def _run_due_jobs(ctx: Ctx, session: Session) -> int:
    return jobs.run_due(ctx.session_factory, ctx.deps, ctx.now_fn, after_each=ctx.renew)


# ====================================================================== 12. alerts

def _retry_alerts(ctx: Ctx, session: Session) -> int:
    alerter = ctx.deps.alerter
    repost = getattr(alerter, "repost", None)
    n = 0
    if repost is not None:
        given_up = select(AlertAttempt.alert_id).where(AlertAttempt.gave_up_at.is_not(None))
        rows = list(session.scalars(select(AlertRow).where(
            AlertRow.posted_at.is_(None), AlertRow.kind != "integration_error",
            AlertRow.created_at <= ctx.now - ALERT_RETRY_AFTER, AlertRow.id.not_in(given_up))
            .order_by(AlertRow.created_at, AlertRow.id).limit(ALERT_RETRY_MAX)))

        def one(row: AlertRow) -> bool:
            att = session.get(AlertAttempt, row.id)
            if att is None:
                att = AlertAttempt(alert_id=row.id, attempts=0)
                session.add(att)
            att.attempts = (att.attempts or 0) + 1
            if not repost(session, row, ctx.now):
                if att.attempts >= ALERT_REPOST_MAX:
                    att.gave_up_at = ctx.now
                    logger.error("alert %s (lead %s): not posted after %s tries; given up", row.id, row.lead_id,
                                 att.attempts)
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
    gave_up = list(session.scalars(select(AlertAttempt).where(
        AlertAttempt.gave_up_at.is_not(None), AlertAttempt.reported_at.is_(None)).order_by(AlertAttempt.alert_id)))
    if not count and not gave_up:
        return 0
    max_id = session.scalar(select(func.max(AlertRow.id)).where(
        AlertRow.posted_at.is_(None), AlertRow.kind == "integration_error", AlertRow.created_at <= ctx.now))
    latest = list(session.scalars(base.order_by(AlertRow.created_at.desc(), AlertRow.id.desc()).limit(DIGEST_LATEST)))
    lines = [f"{count} integration error(s) since the last digest; latest {len(latest)}:"]
    lines += [f"{r.kind} (lead {r.lead_id if r.lead_id is not None else 'none'}): {(r.text or '')[:DIGEST_TEXT_MAX]}"
              for r in latest]
    if gave_up:
        lines.append(f"{len(gave_up)} alert(s) could not be posted after {ALERT_REPOST_MAX} tries (gave up):")
        for att in gave_up[:DIGEST_LATEST]:
            r = session.get(AlertRow, att.alert_id)
            if r is not None:
                lines.append(f"{r.kind} (lead {r.lead_id if r.lead_id is not None else 'none'}): "
                             f"{(r.text or '')[:DIGEST_TEXT_MAX]}")
    _set_mark(session, "integration_digest", ctx.now)
    session.commit()  # one attempt an hour, even if Teams is down
    if not post_text("Integration errors (hourly digest)", lines, ctx.now):
        logger.warning("worker: integration digest not posted (%s error(s) pending)", count)
        return 0
    if max_id is not None:
        session.execute(update(AlertRow).where(
            AlertRow.posted_at.is_(None), AlertRow.kind == "integration_error", AlertRow.id <= max_id)
            .values(posted_at=ctx.now).execution_options(synchronize_session=False))
    for att in gave_up:
        att.reported_at = ctx.now
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


# ====================================================================== 14. email-tool stop / block retries

_TOOL_RETRIES = ((TOOL_STOP_PENDING, "stop"), (TOOL_BLOCK_PENDING, "block"))


def _retry_tool_stops(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(or_(Lead.variant_flags[TOOL_STOP_PENDING].as_string().is_not(None),
                                   Lead.variant_flags[TOOL_BLOCK_PENDING].as_string().is_not(None)))
            .order_by(Lead.id))

    def due_keys(lead: Lead) -> List[Tuple[str, str]]:
        out = []
        for key, method in _TOOL_RETRIES:
            p = _flags(lead).get(key)
            if isinstance(p, dict) and p.get("items"):
                at = _ts(p.get("next_at"))
                if at is None or at <= ctx.now:
                    out.append((key, method))
        return out

    def qualifies(lead: Lead) -> bool:
        return bool(due_keys(lead))

    def one(lead: Lead) -> bool:
        for key, method in due_keys(lead):
            p = dict(_flags(lead)[key])
            fn = getattr(ctx.deps.tool, method, None)
            left = []
            for item in p.get("items") or []:
                try:
                    if fn is None:
                        raise RuntimeError(f"tool has no {method}")
                    with session.begin_nested():
                        fn(item)
                except Exception as exc:  # noqa: BLE001 - kept and retried later
                    logger.warning("lead %s: tool.%s retry failed: %s", lead.id, method, type(exc).__name__)
                    left.append(item)
            if not left:
                flags = dict(_flags(lead))
                flags.pop(key, None)
                lead.variant_flags = flags
                logger.info("lead %s: tool.%s retry succeeded", lead.id, method)
                continue
            attempts = int(p.get("attempts", 1)) + 1
            p.update(items=left, attempts=attempts, next_at=(ctx.now + tool_retry_delay(attempts)).isoformat())
            if attempts - 1 >= TOOL_REALERT_AFTER and not p.get("realerted"):
                p["realerted"] = ctx.now.isoformat()
                set_flag(lead, key, p)
                what = "stop" if method == "stop" else "block-list"
                execute(session, lead, [Alert("tool_stop_failed", tool_stop_failed_text(lead.id, what) +
                                              f" (still failing after {attempts} tries)")], ctx.deps, ctx.now)
            else:
                set_flag(lead, key, p)
        return True

    return _each_lead(ctx, session, "retry_tool_stops", _pick(session, stmt, qualifies, ctx.batch), qualifies, one)


# ====================================================================== 15. verification retries

VERIFY_RETRY_BACKOFF = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
VERIFY_RETRY = "verify_retry"  # {"attempts": n, "next_at": iso, "alerted": iso}


def _verify_next(lead: Lead) -> Optional[datetime]:
    """When the next re-verification is due; None once the retries are used up."""
    r = _flags(lead).get(VERIFY_RETRY) if isinstance(_flags(lead).get(VERIFY_RETRY), dict) else {}
    n = int(r.get("attempts", 0))
    if n >= len(VERIFY_RETRY_BACKOFF):
        return None
    return _ts(r.get("next_at")) or (lead.created_at + VERIFY_RETRY_BACKOFF[0])


def _verified(ctx: Ctx, session: Session, lead: Lead, status: str) -> None:
    """Verification answered: record it, lift the hold (EU leads keep their EU hold) and restart a
    lead that was paused only for it."""
    lead.verify_status = status
    flags = dict(_flags(lead))
    flags.pop(VERIFY_RETRY, None)
    lead.variant_flags = flags
    eu_hold = lead.is_eu and not ctx.settings.eu_email_approved
    lead.email_hold = EU_HOLD if eu_hold else None
    if lead.state == S.PAUSED.value and lead.pause_reason == VERIFY_HOLD:
        if eu_hold:
            lead.pause_reason = EU_HOLD
        else:
            _restart(session, lead, ctx.deps.tool, ctx.now, "verified")  # start_new picks it up
            if status not in EMAIL_OK_STATUSES:  # email-only lead with a bad address: nothing to send
                execute(session, lead, [SetState(S.CLOSED, "invalid_email")], ctx.deps, ctx.now)
    logger.info("lead %s: email verified (%s), hold lifted", lead.id, status)


def _retry_verification(ctx: Ctx, session: Session) -> int:
    verify = ctx.deps.verify
    if verify is None:
        return 0
    stmt = select(Lead).where(Lead.email_hold == VERIFY_HOLD).order_by(Lead.created_at, Lead.id)

    def qualifies(lead: Lead) -> bool:
        if lead.email_hold != VERIFY_HOLD or lead.state in _TERMINAL_VALUES or not lead.email:
            return False
        due = _verify_next(lead)
        return due is not None and due <= ctx.now

    def one(lead: Lead) -> bool:
        status = verify(lead.email)
        if status != VERIFY_ERROR:
            _verified(ctx, session, lead, status)
            return True
        r = dict(_flags(lead).get(VERIFY_RETRY) or {})
        n = int(r.get("attempts", 0)) + 1
        r["attempts"] = n
        if n < len(VERIFY_RETRY_BACKOFF):
            r["next_at"] = (lead.created_at + VERIFY_RETRY_BACKOFF[n]).isoformat()
            set_flag(lead, VERIFY_RETRY, r)
            logger.warning("lead %s: email verification still failing (%s of %s)", lead.id, n,
                           len(VERIFY_RETRY_BACKOFF))
            return True
        r["alerted"] = ctx.now.isoformat()
        set_flag(lead, VERIFY_RETRY, r)
        execute(session, lead, [Alert("verify_failed", (
            f"lead {lead.id}: the email could not be verified after {n} tries (ZeroBounce unreachable, "
            "error or out of credits); the email stays held. Check ZeroBounce, then use: retry "
            f"{lead.id}"))], ctx.deps, ctx.now)
        logger.error("lead %s: email verification gave up after %s tries", lead.id, n)
        return True

    return _each_lead(ctx, session, "retry_verification", _pick(session, stmt, qualifies, ctx.batch), qualifies,
                      one)


# ====================================================================== 16. stuck leads

def _entered_sequence(session: Session, lead: Lead) -> datetime:
    at = session.scalar(select(func.max(Event.occurred_at)).where(
        Event.lead_id == lead.id, Event.kind == "state_change",
        Event.data["to"].as_string() == S.IN_SEQUENCE.value))
    return at or lead.created_at


def _stuck_since(session: Session, lead: Lead) -> Optional[datetime]:
    """When the lead's first send on its active channel was due (None = it has sent)."""
    if lead.active_channel == "email":
        if (lead.step_sent or 0) > 0 or lead.email_hold:
            return None
    elif lead.active_channel == "whatsapp":
        if lead.wa_template_sent_at is not None:
            return None
    switched = _ts((_flags(lead).get("switched") or {}).get("at")) if isinstance(_flags(lead).get("switched"),
                                                                                  dict) else None
    times = [t for t in (_entered_sequence(session, lead), lead.wa_template_sent_at, lead.wa_reminder_sent_at,
                         switched) if t is not None]
    return max(times) if times else None


def _stuck_leads(ctx: Ctx, session: Session) -> int:
    stmt = (select(Lead).where(Lead.state == S.IN_SEQUENCE.value, Lead.created_at <= ctx.now - STUCK_AFTER)
            .order_by(Lead.created_at, Lead.id))

    def qualifies(lead: Lead) -> bool:
        if lead.state != S.IN_SEQUENCE.value or _flags(lead).get("stuck_alerted"):
            return False
        since = _stuck_since(session, lead)
        return since is not None and ctx.now - since > STUCK_AFTER

    def one(lead: Lead) -> bool:
        set_flag(lead, "stuck_alerted", ctx.now.isoformat())
        execute(session, lead, [Alert("stuck_lead", (
            f"lead {lead.id}: in sequence on {lead.active_channel or 'no channel'} but nothing has gone out "
            f"for over {STUCK_AFTER.days} days; check it (Teams: status {lead.id} / retry {lead.id} / "
            f"nurture {lead.id})"))], ctx.deps, ctx.now)
        logger.warning("lead %s: stuck in sequence, alerted", lead.id)
        return True

    return _each_lead(ctx, session, "stuck_leads", _pick(session, stmt, qualifies, ctx.batch), qualifies, one)


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
    ("retry_tool_stops", _retry_tool_stops),
    ("retry_verification", _retry_verification),
    ("stuck_leads", _stuck_leads),
)


# ====================================================================== lock

class _Lock:
    def __init__(self, release: Callable[[], None], renew: Callable[[], None]) -> None:
        self.release, self.renew = release, renew


def _pg_release(conn: Any) -> None:
    """Unlock and close. If the unlock fails the session-level lock may still be held: invalidate the
    connection so the pool discards it (closing the server session frees the lock), never reuses it."""
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PG_LOCK_KEY})
    except Exception as exc:  # noqa: BLE001 - never raise from release
        logger.warning("worker lock unlock failed: %s; dropping the connection", type(exc).__name__)
        try:
            conn.invalidate()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


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

        return _Lock(lambda: _pg_release(conn), lambda: None)

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
         batch: Optional[int] = None, scanners: Optional[Scanners] = None,
         stop: Optional[threading.Event] = None) -> Dict[str, int]:
    """Run every job once. Returns {job name: leads/items handled (-1 = the job failed),
    "failed_jobs": n}, or {"skipped": 1} when another worker holds the lock. When `stop` is set
    (shutdown) the tick ends after the current job, with "stopped": 1."""
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
                  scanners=scanners or _default_scanners(), renew=lock.renew)
        results: Dict[str, int] = {}
        failed = 0
        for name, fn in JOBS:
            if stop is not None and stop.is_set():
                results["stopped"] = 1
                logger.info("worker tick stopped before %s (shutdown)", name)
                break
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
    from .logging_setup import configure as configure_logging

    configure_logging()

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
            tick(factory, deps, now_fn, settings, stop=stop)
        except Exception as exc:  # noqa: BLE001 - keep looping
            logger.error("worker tick crashed: %s", type(exc).__name__)
        stop.wait(settings.worker_interval_s)
    logger.info("worker loop stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
