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
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from .actions import (
    Deps, SetState, StopAllOutreach, ToolStop, WaCancelReminder, execute, record_integration_error,
)
from .channels import fallback_channel
from .config import Settings
from .models import Event, Lead
from .rules import EventSnapshot, LeadSnapshot, decide
from .sequencer import enroll, refresh_next_step, render_reply
from .states import LeadState
from .whatsapp.client import WaSendError, WhatsAppNotAllowed

logger = logging.getLogger(__name__)

S = LeadState

INTRO_TEMPLATE = "ozoo_intro"
REMINDER_TEMPLATE = "ozoo_reminder"
INTRO_ATTEMPT = "wa_intro_attempted_at"
REMINDER_ATTEMPT = "wa_reminder_attempted_at"
# set when the attempt ended in a known failure (handled through decide); reconcile skips it
FAILED_SUFFIX = "_failed"

BeforeSend = Optional[Callable[[], None]]


def set_flag(lead: Lead, key: str, value: Any) -> None:
    flags = dict(lead.variant_flags or {})
    flags[key] = value
    lead.variant_flags = flags


def _flags(lead: Lead) -> dict:
    return lead.variant_flags or {}


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
    "already" (attempted before), "sent", "failed" (Meta refused: handled via decide),
    "not_allowed" (no consent / suppressed: handled via decide) or "error" (outcome unknown)."""
    if _flags(lead).get(attempt_key) or getattr(lead, sent_attr) is not None:
        return "already"
    set_flag(lead, attempt_key, now.isoformat())
    session.flush()
    if before_send is not None:
        before_send()  # the worker commits here, so the attempt is durable before any HTTP
    try:
        with session.begin_nested():
            deps.wa.send_template(session, lead, name, now)
    except WaSendError as exc:
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
    session.flush()
    return "sent"


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
        try:
            with session.begin_nested():
                enroll(session, lead, deps.tool, now, settings=settings)
        except Exception as exc:  # noqa: BLE001 - recorded; the worker's start_new retries
            logger.warning("lead %s: enrol failed: %s", lead.id, type(exc).__name__)
            record_integration_error(session, lead.id, f"lead {lead.id}: enrol failed: {type(exc).__name__}", now)
            return "error"
        return "started"
    if channel == "whatsapp":
        return send_template_once(session, lead, INTRO_TEMPLATE, INTRO_ATTEMPT, "wa_template_sent_at",
                                  deps, now, before_send=before_send)
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
