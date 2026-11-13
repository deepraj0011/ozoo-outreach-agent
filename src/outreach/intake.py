from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .channels import choose_channel, normalize_phone
from .eu import is_eu
from .models import Lead, Suppression
from .states import LeadState, transition

logger = logging.getLogger(__name__)

FREE_MAIL_DOMAINS = frozenset(
    {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com",
     "icloud.com", "proton.me"}
)
# a colleague in one of these states holds new leads at the same company (account rule);
# HANDED_OFF too: sales owns the account
_HOLD_STATES = (LeadState.ENGAGED.value, LeadState.QUALIFYING.value, LeadState.BOOKED.value,
                LeadState.HANDED_OFF.value)
EU_HOLD = "eu_approval"
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


class IntakePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=254)
    phone: Optional[str] = Field(default=None, max_length=200)
    country: Optional[str] = Field(default=None, max_length=200)
    consent_email: bool = False
    consent_whatsapp: bool = False
    consent_at: Optional[datetime] = None
    industry: Optional[str] = Field(default=None, max_length=200)
    size: Optional[str] = Field(default=None, max_length=200)
    timezone_group: Optional[str] = Field(default=None, max_length=200)


class IntakeResult:
    def __init__(self, lead_id: Optional[int], outcome: str,
                 channel: Optional[str] = None, reason: Optional[str] = None) -> None:
        self.lead_id = lead_id
        self.outcome = outcome
        self.channel = channel
        self.reason = reason

    def __repr__(self) -> str:
        return f"IntakeResult({self.lead_id}, {self.outcome}, {self.channel}, {self.reason})"


def _rejected(reason: str) -> IntakeResult:
    return IntakeResult(None, "rejected", None, reason)


def _find_existing(session: Session, email: Optional[str], phone: Optional[str]):
    clauses = []
    if email:
        clauses.append(Lead.email == email)
    if phone:
        clauses.append(Lead.phone == phone)
    return session.scalar(select(Lead).where(or_(*clauses)).limit(1))


def intake_lead(session: Session, payload: dict, verifier, now: datetime,
                primary_when_both: str = "whatsapp", eu_email_approved: bool = False) -> IntakeResult:
    try:
        p = IntakePayload.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(x) for x in e["loc"]) or "payload" for e in exc.errors()})
        return _rejected("invalid field: " + ", ".join(fields))
    except Exception:  # noqa: BLE001 - never raise to the caller
        return _rejected("invalid payload")

    email: Optional[str] = p.email.lower() if p.email else None
    if email and not _EMAIL_RE.match(email):
        email = None
    phone = normalize_phone(p.phone, p.country) if p.phone else None
    if not (p.email or p.phone):
        return _rejected("missing contact: email or phone")
    if not email and not phone:
        return _rejected("no valid email or phone")

    # suppression (opt-out covers all channels) comes before dedup
    contacts = [c for c in (email, phone) if c]
    if session.scalar(select(Suppression.id).where(Suppression.value.in_(contacts)).limit(1)):
        return IntakeResult(None, "skipped_suppressed", None, "suppressed")

    existing = _find_existing(session, email, phone)
    if existing is not None:
        return IntakeResult(existing.id, "skipped_duplicate", None, "duplicate")

    # cold email needs no opt-in, so every email is verified (not only consented ones)
    verify_status: Optional[str] = verifier.check(email) if email else None
    eu = is_eu(p.country, email, phone)

    lead = Lead(
        email=email,
        phone=phone,
        name=p.name,
        company=p.company,
        domain=email.split("@", 1)[1] if email else None,
        role=p.role,
        source=p.source,
        consent_email=p.consent_email,
        consent_whatsapp=p.consent_whatsapp,
        consent_at=p.consent_at,
        industry=p.industry,
        size=p.size,
        timezone_group=p.timezone_group,
        verify_status=verify_status,
        is_eu=eu,
        email_hold=EU_HOLD if eu and not eu_email_approved else None,
        state=LeadState.INTAKE.value,
        score=0,
        step_sent=0,
        channel_switched=False,
        created_at=now,
    )
    channel = choose_channel(lead, primary_when_both)
    eu_held = False
    if channel is None and lead.email_hold:
        lead.email_hold = None
        if choose_channel(lead, primary_when_both) == "email":
            channel, eu_held = "email", True  # valid email, waiting for EU approval
        lead.email_hold = EU_HOLD
    if channel is None:
        return _rejected("no usable channel")

    lead.active_channel = channel
    try:
        with session.begin_nested():
            session.add(lead)
            session.flush()
    except IntegrityError:
        # concurrent intake of the same contact won the unique constraint
        existing = _find_existing(session, email, phone)
        return IntakeResult(existing.id if existing else None, "skipped_duplicate",
                            None, "duplicate")

    if eu_held:
        lead.pause_reason = EU_HOLD
        transition(session, lead, LeadState.PAUSED, "eu_hold", now)
        logger.info("lead %s held until EU email is approved", lead.id)
        return IntakeResult(lead.id, "held_eu", channel, EU_HOLD)

    blocker: Optional[int] = None
    if lead.domain and lead.domain not in FREE_MAIL_DOMAINS:
        blocker = session.scalar(
            select(Lead.id).where(
                Lead.domain == lead.domain, Lead.id != lead.id, Lead.state.in_(_HOLD_STATES)
            ).order_by(Lead.id).limit(1)
        )
    if blocker is not None:
        lead.pause_reason = "account"
        lead.variant_flags = {**(lead.variant_flags or {}), "paused_by": blocker}
        transition(session, lead, LeadState.PAUSED, "account_hold", now)
        logger.info("lead %s held by account rule", lead.id)
        return IntakeResult(lead.id, "held_account", channel, "account")

    transition(session, lead, LeadState.IN_SEQUENCE, "intake_accepted", now)
    logger.info("lead %s accepted on %s", lead.id, channel)
    return IntakeResult(lead.id, "accepted", channel, None)
