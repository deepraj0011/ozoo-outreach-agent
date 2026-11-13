"""Shared event ingest for every channel: match -> store idempotently -> verdict -> decide -> execute.

Used by the email webhook and unsubscribe page (Task 6), WhatsApp (Task 8) and Cal.com (Task 11).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .actions import Deps, execute, record_integration_error
from .botfilter import Scanners, classify_event
from .emailtool.base import NormalizedEvent
from .intake import FREE_MAIL_DOMAINS
from .models import ChatMessage, Event, Lead
from .rules import EventSnapshot, LeadSnapshot, decide
from .states import TERMINAL

logger = logging.getLogger(__name__)

WA_WINDOW = timedelta(hours=24)
WA_INBOUND_KINDS = frozenset({"wa_text", "wa_button"})
HUMAN_KINDS = frozenset({"reply", "wa_read"}) | WA_INBOUND_KINDS
UNMATCHED_ALERTS = {"reply": "unmatched_reply", "wa_text": "unmatched_inbound", "wa_button": "unmatched_inbound",
                    "booking_created": "unmatched_booking"}
_TERMINAL_VALUES = tuple(s.value for s in TERMINAL)
UA_MAX = 512
IP_MAX = 64


def match_lead(session: Session, ev: NormalizedEvent) -> Tuple[Optional[Lead], Optional[str]]:
    """lead_id -> tool_lead_id -> exact email -> exact phone -> (reply only) company domain.

    Returns (lead, how) with how in lead_id|tool_lead_id|email|phone|domain, or (None, None).
    """
    if ev.lead_id is not None:
        lead = session.get(Lead, ev.lead_id)
        if lead is not None:
            return lead, "lead_id"
    if ev.tool_lead_id:
        lead = session.scalar(select(Lead).where(Lead.tool_lead_id == ev.tool_lead_id).limit(1))
        if lead is not None:
            return lead, "tool_lead_id"
    email = ev.email.strip().lower() if ev.email else None
    if email:
        lead = session.scalar(select(Lead).where(Lead.email == email).limit(1))
        if lead is not None:
            return lead, "email"
    if ev.phone:
        lead = session.scalar(select(Lead).where(Lead.phone == ev.phone).limit(1))
        if lead is not None:
            return lead, "phone"
    if ev.kind == "reply" and email and "@" in email:
        domain = email.rsplit("@", 1)[1]
        if domain not in FREE_MAIL_DOMAINS:
            lead = session.scalar(
                select(Lead)
                .where(Lead.domain == domain, Lead.state.not_in(_TERMINAL_VALUES))
                .order_by(Lead.created_at.desc(), Lead.id.desc())
                .limit(1)
            )
            if lead is not None:
                return lead, "domain"
    return None, None


def _verdict(row: Event, lead: Optional[Lead], scanners: Scanners) -> Optional[str]:
    if row.kind == "open":
        sent_at = None
        if lead is not None and row.step is not None and row.step == lead.step_sent:
            sent_at = lead.last_sent_at
        return classify_event(row, sent_at, [], scanners)
    if row.kind in HUMAN_KINDS:
        return "human"
    if row.kind == "click":
        return None  # the worker classifies settled clicks (needs sibling clicks)
    return "n/a"


def _insert(session: Session, row: Event) -> bool:
    """Insert inside a savepoint; False if provider_event_id was already stored."""
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:
        logger.info("duplicate %s event %s skipped", row.kind, row.provider_event_id)
        return False
    return True


def _ingest_one(session: Session, ev: NormalizedEvent, deps: Deps, now: datetime, scanners: Scanners) -> None:
    lead, how = match_lead(session, ev)
    data = dict(ev.data or {})
    if how == "domain":
        # a colleague replied: keep who actually wrote (DB only, never logged)
        data["matched_by"] = "domain"
        data["sender_email"] = ev.email.strip().lower() if ev.email else None
    row = Event(
        lead_id=lead.id if lead is not None else None,
        channel=ev.channel,
        kind=ev.kind,
        provider_event_id=ev.provider_event_id,
        occurred_at=ev.occurred_at,
        received_at=now,
        step=ev.step,
        tag=ev.tag,
        ip=ev.ip[:IP_MAX] if ev.ip else None,
        user_agent=ev.user_agent[:UA_MAX] if ev.user_agent else None,
        processed=False,
        data=data,
    )
    if not _insert(session, row):
        return
    row.verdict = _verdict(row, lead, scanners)

    if lead is None:
        alert_kind = UNMATCHED_ALERTS.get(ev.kind)
        if alert_kind:
            sender = ev.email or ev.phone or "unknown sender"
            # Sender address goes to the Alert row / Teams (humans must find it), never to logs.
            try:
                with session.begin_nested():
                    deps.alerter.send(alert_kind, f"{ev.kind} event #{row.id} from {sender} matches no lead",
                                      None, now, session)
            except Exception as exc:  # noqa: BLE001 - same isolation as executor port calls
                logger.warning("event %s: alerter.send failed: %s", row.id, type(exc).__name__)
                record_integration_error(session, None, f"event {row.id}: alerter.send failed: "
                                         f"{type(exc).__name__}", now)
        logger.info("event %s (%s) matched no lead", row.id, ev.kind)
        row.processed = True
        return

    if ev.kind in WA_INBOUND_KINDS:
        # ruling 10: open the window BEFORE decide (rules read wa_window_open)
        until = ev.occurred_at + WA_WINDOW
        if lead.wa_window_until is None or lead.wa_window_until < until:
            lead.wa_window_until = until
        session.add(ChatMessage(
            lead_id=lead.id, direction="in", author="lead",
            text=str(row.data.get("text") or row.data.get("payload") or ""),
            wa_message_id=row.data.get("wamid"), created_at=ev.occurred_at,
        ))

    if ev.kind == "click":
        # Scanner-safe: clicks are settled + decided by the worker; unsubscribe link clicks only
        # lead to the confirm page and are never acted on.
        row.processed = ev.tag == "unsubscribe"
        return

    actions = decide(LeadSnapshot.from_lead(lead, now), EventSnapshot.from_event(row), now)
    execute(session, lead, actions, deps, now, event=row)
    row.processed = True


def ingest(session: Session, events: Iterable[NormalizedEvent], deps: Deps, now: datetime,
           scanners: Scanners) -> None:
    """One transaction per event (this function commits). Duplicates are skipped. Any error in
    decide/execute rolls the event back and re-raises, so the provider's retry is safe."""
    for ev in events:
        try:
            _ingest_one(session, ev, deps, now, scanners)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.error("ingest of %s event %s failed: %s", ev.kind, ev.provider_event_id,
                         type(exc).__name__)
            raise
