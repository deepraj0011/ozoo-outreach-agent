from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime


class Lead(Base):
    __tablename__ = "lead"
    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR phone IS NOT NULL", name="ck_lead_email_or_phone"),
        Index("ix_lead_domain", "domain"),
        Index("ix_lead_state", "state"),
        Index("ix_lead_tool_lead_id", "tool_lead_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    domain: Mapped[Optional[str]] = mapped_column(String(255))
    role: Mapped[Optional[str]] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(100))

    consent_email: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    industry: Mapped[Optional[str]] = mapped_column(String(100))
    size: Mapped[Optional[str]] = mapped_column(String(50))
    timezone_group: Mapped[Optional[str]] = mapped_column(String(50))
    verify_status: Mapped[Optional[str]] = mapped_column(String(50))
    is_eu: Mapped[bool] = mapped_column(Boolean, default=False)
    # set (e.g. "eu_approval") while email must not be sent; cleared when approved
    email_hold: Mapped[Optional[str]] = mapped_column(String(50))

    state: Mapped[str] = mapped_column(String(20), default="intake")
    pause_reason: Mapped[Optional[str]] = mapped_column(String(100))
    pause_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    score: Mapped[int] = mapped_column(Integer, default=0)
    last_human_event_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    active_channel: Mapped[Optional[str]] = mapped_column(String(10))  # email|whatsapp|None
    channel_switched: Mapped[bool] = mapped_column(Boolean, default=False)

    step_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    tool_lead_id: Mapped[Optional[str]] = mapped_column(String(100))

    wa_template_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    wa_reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    wa_window_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    wa_bot_turns: Mapped[int] = mapped_column(Integer, default=0)
    # Entra (AAD) object id of the salesperson who took the chat over in Teams; display name next
    wa_human_owner: Mapped[Optional[str]] = mapped_column(String(100))
    wa_human_owner_name: Mapped[Optional[str]] = mapped_column(String(100))

    variant_flags: Mapped[Dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)
    nurture_track: Mapped[Optional[str]] = mapped_column(String(50))
    nurture_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_lead_kind", "lead_id", "kind"),
        Index("ix_event_processed", "processed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lead.id"))
    channel: Mapped[str] = mapped_column(String(10))  # email|whatsapp|web|cal|system
    kind: Mapped[str] = mapped_column(String(50))
    provider_event_id: Mapped[Optional[str]] = mapped_column(String(200), unique=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)
    step: Mapped[Optional[int]] = mapped_column(Integer)
    tag: Mapped[Optional[str]] = mapped_column(String(50))
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    verdict: Mapped[Optional[str]] = mapped_column(String(20))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[Dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), default=dict)


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (Index("ix_chat_message_lead", "lead_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead.id"))
    direction: Mapped[str] = mapped_column(String(3))  # in|out
    author: Mapped[str] = mapped_column(String(10))  # lead|bot|human
    text: Mapped[str] = mapped_column(Text)
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Suppression(Base):
    __tablename__ = "suppression"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(320), unique=True)
    kind: Mapped[str] = mapped_column(String(10))  # email|phone
    reason: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (Index("ix_alert_lead_id", "lead_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lead.id"))
    kind: Mapped[str] = mapped_column(String(50))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    posted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)  # NULL = not on Teams yet
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class Job(Base):
    """A background LLM call (reply classification or a chatbot answer), run after the webhook
    request by outreach.jobs. last_error holds an exception type name only, never prompt or lead text."""
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status_run_after", "status", "run_after"),
                      Index("ix_job_lead_kind", "lead_id", "kind"))

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead.id"))
    kind: Mapped[str] = mapped_column(String(20))  # classify|bot_reply
    event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("event.id"))
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    last_error: Mapped[Optional[str]] = mapped_column(String(200))
