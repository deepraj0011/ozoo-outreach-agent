"""Channel starter and switcher: the real actions.OutreachPort (Task 12).

start_outreach() starts a lead on its active channel only (one channel sends at a time):
email -> sequencer.enroll (never while an EU hold is set); WhatsApp -> the `ozoo_intro` template.

A WhatsApp template is sent at most once per lead. `variant_flags["wa_intro_attempted_at"]`
(and `wa_reminder_attempted_at`) is written and flushed BEFORE the send; while it is set the
template is never sent again. A send whose outcome is unknown (crash, timeout after Meta took it)
is reconciled by the worker from the wa_sent / wa_delivered / wa_read status webhooks.

A send Meta refuses (WaSendError) becomes a synthetic `wa_failed` event that goes through
rules.decide() and execute(): that is how the WhatsApp -> email fallback happens.

Flushes only; the caller commits. Logs carry lead ids and error types / codes, never contacts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actions import (
    Deps, SetState, StopAllOutreach, ToolStop, WaCancelReminder, execute, record_integration_error,
)
from .channels import fallback_channel
from .config import Settings
from .models import Event, Lead, Suppression
from .rules import EventSnapshot, LeadSnapshot, decide
from .sequencer import enroll, refresh_next_step, render_reply
from .states import TERMINAL_VALUES, LeadState
from .whatsapp.client import WaSendError, WhatsAppNotAllowed

logger = logging.getLogger(__name__)

S = LeadState

INTRO_TEMPLATE = "ozoo_intro"
REMINDER_TEMPLATE = "ozoo_reminder"
INTRO_ATTEMPT = "wa_intro_attempted_at"
REMINDER_ATTEMPT = "wa_reminder_attempted_at"
# set when the attempt ended in a known failure (handled through decide); reconcile skips it
FAILED_SUFFIX = "_failed"
# Meta codes that mean "try again later" (rate limits, temporary errors): the attempt flag is cleared
# and the send retried after WA_RETRY_BACKOFF; after that many transient failures it counts as permanent.
WA_TRANSIENT_CODES = frozenset({130429, 131056, 131048, 131000, 1, 2, 4})
WA_RETRY_BACKOFF = (timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6))
WA_RETRY_AFTER = "wa_retry_after"
TRANSIENT_SUFFIX = "_transient"
# written and committed before the email tool's add_lead; while set without a tool lead id, the next
# start looks the lead up in the tool (find_lead) before adding it again
EMAIL_ENROLL_ATTEMPT = "email_enroll_attempted_at"
_TERMINAL_VALUES = TERMINAL_VALUES  # states.TERMINAL as strings

BeforeSend = Optional[Callable[[], None]]


def set_flag(lead: Lead, key: str, value: Any) -> None:
    flags = dict(lead.variant_flags or {})
    flags[key] = value
    lead.variant_flags = flags


def _flags(lead: Lead) -> dict:
    return lead.variant_flags or {}


def pop_flags(lead: Lead, *keys: str) -> None:
    flags = dict(lead.variant_flags or {})
    for k in keys:
        flags.pop(k, None)
    lead.variant_flags = flags


def email_suppressed(session: Session, email: Optional[str]) -> bool:
    if not email:
        return False
    return session.scalar(select(Suppression.id).where(
        Suppression.value == email, Suppression.kind == "email").limit(1)) is not None


def _ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------- WhatsApp failures

def handle_wa_send_failure(session: Session, lead: Lead, code: Optional[int], template: str,
                           deps: Deps, now: datetime) -> None:
    """Store a synthetic wa_failed event and let the rules decide (fallback / close / alert)."""
    row = Event(lead_id=lead.id, channel="whatsapp", kind="wa_failed", occurred_at=now, received_at=now,
                verdict="n/a", processed=True, data={"code": code, "template": template, "source": "send"})
    session.add(row)
    session.flush()
    actions = decide(LeadSnapshot.from_lead(lead, now), EventSnapshot.from_event(row), now)
    execute(session, lead, actions, deps, now, event=row)


def send_template_once(session: Session, lead: Lead, name: str, attempt_key: str, sent_attr: str,
                       deps: Deps, now: datetime, *, before_send: BeforeSend = None) -> str:
    """Send WhatsApp template `name` at most once. Never raises. Returns
    "already" (attempted before), "wait" (a transient failure's back-off has not passed), "sent",
    "retry" (transient Meta error: attempt cleared, retried after WA_RETRY_BACKOFF), "failed" (Meta
    refused: handled via decide), "not_allowed" (no consent / suppressed: handled via decide) or
    "error" (outcome unknown, including a 2xx without a message id: never resent)."""
    if _flags(lead).get(attempt_key) or getattr(lead, sent_attr) is not None:
        return "already"
    retry_at = _ts(_flags(lead).get(WA_RETRY_AFTER))
    if retry_at is not None and retry_at > now:
        return "wait"
    set_flag(lead, attempt_key, now.isoformat())
    session.flush()
    if before_send is not None:
        before_send()  # the worker commits here, so the attempt is durable before any HTTP
    try:
        with session.begin_nested():
            deps.wa.send_template(session, lead, name, now)
    except WaSendError as exc:
        if exc.code is None and 200 <= exc.status < 300:
            # Meta took the request but gave no message id: it may have been sent. Never resend;
            # the worker reconciles it from status webhooks (and gives up after 24 h).
            logger.warning("lead %s: WhatsApp template %s: HTTP %s without message id", lead.id, name, exc.status)
            record_integration_error(session, lead.id, f"lead {lead.id}: WhatsApp template {name}: no message "
                                                       "id in the answer (not resent)", now)
            return "error"
        transient = int(_flags(lead).get(attempt_key + TRANSIENT_SUFFIX, 0)) + 1
        if exc.code in WA_TRANSIENT_CODES and transient <= len(WA_RETRY_BACKOFF):
            retry_at = now + WA_RETRY_BACKOFF[transient - 1]
            pop_flags(lead, attempt_key)
            set_flag(lead, attempt_key + TRANSIENT_SUFFIX, transient)
            set_flag(lead, WA_RETRY_AFTER, retry_at.isoformat())
            session.flush()
            logger.warning("lead %s: WhatsApp template %s transient error (code %s, %s of %s); retry at %s",
                           lead.id, name, exc.code, transient, len(WA_RETRY_BACKOFF), retry_at.isoformat())
            return "retry"
        logger.warning("lead %s: WhatsApp template %s refused (code %s)", lead.id, name, exc.code)
        set_flag(lead, attempt_key + FAILED_SUFFIX, exc.code if exc.code is not None else "unknown")
        handle_wa_send_failure(session, lead, exc.code, name, deps, now)
        return "failed"
    except WhatsAppNotAllowed:
        logger.info("lead %s: WhatsApp template %s not allowed", lead.id, name)
        set_flag(lead, attempt_key + FAILED_SUFFIX, "not_allowed")
        handle_wa_send_failure(session, lead, None, name, deps, now)
        return "not_allowed"
    except Exception as exc:  # noqa: BLE001 - maybe sent: never resend, the worker reconciles
        logger.warning("lead %s: WhatsApp template %s outcome unknown: %s", lead.id, name, type(exc).__name__)
        record_integration_error(session, lead.id, f"lead {lead.id}: WhatsApp template {name} failed: "
                                                   f"{type(exc).__name__} (not resent)", now)
        return "error"
    if getattr(lead, sent_attr) is None:  # the real sender sets it; a bare port may not
        setattr(lead, sent_attr, now)
    pop_flags(lead, WA_RETRY_AFTER, attempt_key + TRANSIENT_SUFFIX)
    session.flush()
    return "sent"


def wa_gave_up(session: Session, lead: Lead, deps: Deps, now: datetime, *,
               settings: Optional[Settings] = None) -> Optional[str]:
    """WhatsApp failed for good and decide() left the lead in sequence on WhatsApp: move on now
    (email if there is one, else NURTURE "wa_failed"), never leave it waiting forever."""
    if lead.state != S.IN_SEQUENCE.value or lead.active_channel != "whatsapp":
        return None
    target = switch_channel(session, lead, "wa_failed", deps, now, settings=settings)
    logger.info("lead %s: WhatsApp failed, moved to %s", lead.id, target or "nurture")
    return target or "nurture"


# ---------------------------------------------------------------------- start / switch

def start_outreach(session: Session, lead: Lead, deps: Deps, now: datetime, *,
                   settings: Optional[Settings] = None, before_send: BeforeSend = None) -> str:
    """Start the lead on its active channel. Never raises. Returns
    "started" (email enrolled), "sent" / "failed" / "not_allowed" / "error" / "already" (WhatsApp),
    "held" (EU email hold), "error" (enrolment failed; the worker retries),
    "not_in_sequence" or "no_channel"."""
    if lead.state != S.IN_SEQUENCE.value:
        return "not_in_sequence"
    settings = settings or getattr(deps.outreach, "settings", None)
    channel = lead.active_channel
    if channel == "email":
        if lead.email_hold:
            logger.info("lead %s: email held (%s), not enrolled", lead.id, lead.email_hold)
            return "held"
        if lead.tool_lead_id:
            return "already"
        if email_suppressed(session, lead.email):
            logger.info("lead %s: email suppressed, not enrolled", lead.id)
            return "suppressed"
        find = getattr(deps.tool, "find_lead", None)
        if _flags(lead).get(EMAIL_ENROLL_ATTEMPT) and find is not None and lead.email:
            # an earlier add_lead may have reached the tool before we lost its answer: look first
            try:
                with session.begin_nested():
                    found = find(lead.email)
            except Exception as exc:  # noqa: BLE001 - unknown: do not add twice; retried
                logger.warning("lead %s: tool lookup failed: %s", lead.id, type(exc).__name__)
                record_integration_error(session, lead.id, f"lead {lead.id}: tool lookup failed: "
                                                           f"{type(exc).__name__}", now)
                return "error"
            if found:
                lead.tool_lead_id = str(found)
                pop_flags(lead, EMAIL_ENROLL_ATTEMPT)
                set_flag(lead, "enrolled_at", now.isoformat())
                session.flush()
                logger.info("lead %s: already in the email tool, enrolment recovered", lead.id)
                return "started"
        set_flag(lead, EMAIL_ENROLL_ATTEMPT, now.isoformat())
        session.flush()
        if before_send is not None:
            before_send()  # the worker commits: the marker is durable before add_lead
        try:
            with session.begin_nested():
                enroll(session, lead, deps.tool, now, settings=settings)
        except Exception as exc:  # noqa: BLE001 - recorded; the worker's start_new retries
            logger.warning("lead %s: enrol failed: %s", lead.id, type(exc).__name__)
            record_integration_error(session, lead.id, f"lead {lead.id}: enrol failed: {type(exc).__name__}", now)
            return "error"
        pop_flags(lead, EMAIL_ENROLL_ATTEMPT)
        session.flush()
        return "started"
    if channel == "whatsapp":
        result = send_template_once(session, lead, INTRO_TEMPLATE, INTRO_ATTEMPT, "wa_template_sent_at",
                                    deps, now, before_send=before_send)
        if result in ("failed", "not_allowed"):
            wa_gave_up(session, lead, deps, now, settings=settings)
        return result
    logger.warning("lead %s: no active channel to start", lead.id)
    return "no_channel"


def switch_channel(session: Session, lead: Lead, reason: str, deps: Deps, now: datetime, *,
                   settings: Optional[Settings] = None) -> Optional[str]:
    """Switch to the other channel, once. Returns the new channel, or None when the lead had no
    fallback (or had switched already) and went to NURTURE."""
    target = fallback_channel(lead)
    if target is None:
        actions: list = [StopAllOutreach()]
        if lead.state != S.NURTURE.value:
            actions.append(SetState(S.NURTURE, reason))
        execute(session, lead, actions, deps, now)
        logger.info("lead %s: no channel to switch to (%s), nurture", lead.id, reason)
        return None
    # stop the current channel first: only one channel sends at a time
    stop = [ToolStop()] if lead.active_channel == "email" else [WaCancelReminder()]
    execute(session, lead, stop, deps, now)
    previous = lead.active_channel
    lead.active_channel = target
    lead.channel_switched = True
    set_flag(lead, "switched", {"from": previous, "reason": reason, "at": now.isoformat()})
    session.flush()
    logger.info("lead %s: channel %s -> %s (%s)", lead.id, previous, target, reason)
    if lead.state == S.IN_SEQUENCE.value:
        start_outreach(session, lead, deps, now, settings=settings)
    return target


# ---------------------------------------------------------------------- the port

class Outreach:
    """actions.OutreachPort. Needs the Deps it belongs to (bind() after building Deps)."""

    def __init__(self, settings: Settings, deps: Optional[Deps] = None) -> None:
        self.settings = settings
        self.deps: Optional[Deps] = deps

    def bind(self, deps: Deps) -> "Outreach":
        self.deps = deps
        return self

    def _deps(self) -> Deps:
        if self.deps is None:
            raise RuntimeError("Outreach port not bound to Deps")
        return self.deps

    def start(self, session: Session, lead: Lead, now: datetime, *, before_send: BeforeSend = None) -> str:
        return start_outreach(session, lead, self._deps(), now, settings=self.settings, before_send=before_send)

    def switch(self, session: Session, lead: Lead, reason: str, now: datetime) -> Optional[str]:
        return switch_channel(session, lead, reason, self._deps(), now, settings=self.settings)

    def refresh_next_step(self, session: Session, lead: Lead) -> None:
        refresh_next_step(session, lead, self._deps().tool, settings=self.settings)

    def send_email_text(self, session: Session, lead: Lead, key: str, now: datetime) -> bool:
        """Reply `key` (booking_link / booking_slots / reschedule) in the lead's email thread. If the
        tool cannot (no thread yet, or reply-in-thread unsupported), ask sales to email personally."""
        deps = self._deps()
        if lead.state in _TERMINAL_VALUES or not lead.email or email_suppressed(session, lead.email):
            # defence in depth: rules never ask for this, but nothing reaches an opted-out lead
            logger.info("lead %s: email reply %s refused (terminal or suppressed)", lead.id, key)
            return False
        sent = False
        if lead.tool_lead_id:
            body = render_reply(lead, key, settings=self.settings)
            sent = bool(deps.tool.reply(lead.tool_lead_id, body))
        if sent:
            logger.info("lead %s: email reply %s sent", lead.id, key)
            return True
        logger.info("lead %s: email reply %s not possible, alerting sales", lead.id, key)
        deps.alerter.send("unsure", f"lead {lead.id}: could not send the '{key}' reply in the email thread; "
                                    "please email the lead personally", lead, now, session)
        return False
