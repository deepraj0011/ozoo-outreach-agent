"""Releasing held leads: colleagues paused by the account rule, and EU leads waiting for
email approval. Called by the worker tick (Task 12); no I/O except the email tool resume.

Account rule (plan section 11): when one person at a company engages, colleagues pause.
When they restart depends on what happened to that person (the "blocker"):

  blocker outcome                         colleagues
  -------------------------------------   ------------------------------------------
  still talking (ENGAGED)                 restart after 14 days of silence from them
  qualifying / booked / handed to sales   stay paused (sales owns the account)
  "not now" (NURTURE)                     restart on their "later" date (else +90 days)
  sales rejected the meeting              restart now
  wrong person (CLOSED)                   restart now
  not interested / unsubscribed / other   restart after 90 days
  no longer active (archived, back in
  sequence, paused, gone)                 restart now
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .intake import _HOLD_STATES, EU_HOLD, FREE_MAIL_DOMAINS
from .models import Event, Lead
from .states import LeadState, transition

logger = logging.getLogger(__name__)

S = LeadState

QUIET_RESTART = timedelta(days=14)
COOL_OFF = timedelta(days=90)
RESTART_NOW_REASONS = frozenset({"sales_rejected", "wrong_person"})
STAY_PAUSED = frozenset({S.QUALIFYING, S.BOOKED, S.HANDED_OFF})
INBOUND_KINDS = ("reply", "wa_text", "wa_button", "booking_created")


def colleague_resume_at(state: LeadState, reason: Optional[str], since: datetime,
                        last_inbound: Optional[datetime],
                        nurture_until: Optional[datetime]) -> Optional[datetime]:
    """Pure. When colleagues blocked by a lead in `state` (entered at `since` for `reason`)
    may restart. None = stay paused."""
    if state in STAY_PAUSED:
        return None
    if state == S.ENGAGED:
        return max(since, last_inbound or since) + QUIET_RESTART
    if reason in RESTART_NOW_REASONS:
        return since
    if state == S.NURTURE:
        return nurture_until or since + COOL_OFF
    if state in (S.CLOSED, S.SUPPRESSED):
        return since + COOL_OFF
    return since  # archived / rejected / back in sequence / paused: no longer blocking


def _last_change(session: Session, lead_id: int):
    ev = session.scalars(
        select(Event).where(Event.lead_id == lead_id, Event.kind == "state_change")
        .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(1)
    ).first()
    if ev is None:
        return None, None
    return ev.occurred_at, (ev.data or {}).get("reason")


def _blocker_resume_at(session: Session, blocker: Lead, now: datetime) -> Optional[datetime]:
    since, reason = _last_change(session, blocker.id)
    last_inbound = session.scalar(
        select(func.max(Event.occurred_at)).where(
            Event.lead_id == blocker.id, Event.kind.in_(INBOUND_KINDS))
    )
    return colleague_resume_at(S(blocker.state), reason, since or blocker.created_at or now,
                               last_inbound, blocker.nurture_until)


def _blockers(session: Session, lead: Lead) -> List[Lead]:
    ids = set()
    paused_by = (lead.variant_flags or {}).get("paused_by")
    if isinstance(paused_by, int):
        ids.add(paused_by)
    if lead.domain and lead.domain not in FREE_MAIL_DOMAINS:
        ids |= set(session.scalars(
            select(Lead.id).where(Lead.domain == lead.domain, Lead.id != lead.id,
                                  Lead.state.in_(_HOLD_STATES))
        ).all())
    if not ids:
        return []
    return list(session.scalars(select(Lead).where(Lead.id.in_(ids))).all())


def account_resume_at(session: Session, lead: Lead, now: datetime) -> Optional[datetime]:
    """When an account-paused lead may restart: the latest time over all its blockers.
    None = stay paused. No blockers left = now."""
    times = []
    for b in _blockers(session, lead):
        t = _blocker_resume_at(session, b, now)
        if t is None:
            return None
        times.append(t)
    return max(times) if times else now


def _restart(session: Session, lead: Lead, tool: Any, now: datetime, reason: str, settings: Any = None) -> None:
    transition(session, lead, S.IN_SEQUENCE, reason, now)
    if (lead.variant_flags or {}).get("p0"):
        from .p0.planner import shift_lead  # p0 imports this package's modules
        shift_lead(session, lead, now, settings=settings)  # overdue emails go in the next slot, gaps kept
    lead.pause_reason = None
    flags = dict(lead.variant_flags or {})
    flags.pop("paused_by", None)
    flags.pop("wa_reminder_cancelled", None)
    lead.variant_flags = flags
    if lead.tool_lead_id and tool is not None:
        try:
            with session.begin_nested():
                tool.resume(lead.tool_lead_id)
        except Exception as exc:  # noqa: BLE001 - logged and alerted, never blocks the tick
            from .actions import record_integration_error
            logger.warning("lead %s: tool.resume failed: %s", lead.id, type(exc).__name__)
            record_integration_error(session, lead.id,
                                     f"lead {lead.id}: tool.resume failed: {type(exc).__name__}", now)
    # never-started leads (held at intake) are picked up by the worker's start_new job


def resume_account_holds(session: Session, tool: Any, now: datetime, settings: Any = None) -> int:
    """Restart colleagues whose blockers allow it. Returns how many restarted."""
    paused: Iterable[Lead] = session.scalars(
        select(Lead).where(Lead.state == S.PAUSED.value, Lead.pause_reason == "account")
        .order_by(Lead.id)
    ).all()
    n = 0
    for lead in paused:
        at = account_resume_at(session, lead, now)
        if at is not None and at <= now:
            _restart(session, lead, tool, now, "account_resume", settings)
            logger.info("lead %s restarted: account hold released", lead.id)
            n += 1
    session.flush()
    return n


def release_eu_holds(session: Session, tool: Any, now: datetime) -> int:
    """Once EU_EMAIL_APPROVED is on: clear the email hold on every EU lead and start the
    leads that were waiting only for it (the account rule still applies)."""
    held = session.scalars(select(Lead).where(Lead.email_hold == EU_HOLD).order_by(Lead.id)).all()
    n = 0
    for lead in held:
        lead.email_hold = None
        if lead.state == S.PAUSED.value and lead.pause_reason == EU_HOLD:
            blockers = _blockers(session, lead)
            if blockers:
                lead.pause_reason = "account"
                lead.variant_flags = {**(lead.variant_flags or {}), "paused_by": blockers[0].id}
            else:
                _restart(session, lead, tool, now, "eu_approved")
                n += 1
    session.flush()
    return n
