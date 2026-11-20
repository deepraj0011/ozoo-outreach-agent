from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false,
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


class AlertAttempt(Base):
    """Worker re-post attempts for an unposted alert (its own table: create_all adds it to an existing
    database, no migration). After ALERT_REPOST_MAX tries the alert is given up and listed once in
    the integration digest."""
    __tablename__ = "alert_attempt"

    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.id"), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    gave_up_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    reported_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)  # in a digest


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


class WorkerLock(Base):
    """Lease row: only one worker tick runs at a time (SQLite; Postgres uses an advisory lock)."""
    __tablename__ = "worker_lock"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WorkerMark(Base):
    """When a periodic worker job (hourly digest, daily mailbox check) last ran."""
    __tablename__ = "worker_mark"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)


class EmailTemplate(Base):
    """Email templates managed in the web app (task 22). Custom rows hold a whole version (a
    sequence step version or a one-off email); a `builtin` row only switches a version from
    templates/emails.yaml off or on again (key builtin.s<step>.<variant>.<index>)."""
    __tablename__ = "email_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    source: Mapped[str] = mapped_column(String(10), default="custom")  # custom|builtin
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|approved|off
    name: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(10), default="sequence")  # sequence|manual
    step: Mapped[Optional[int]] = mapped_column(Integer)
    variant: Mapped[str] = mapped_column(String(20), default="standard")
    angle: Mapped[str] = mapped_column(String(30), default="any")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    rich: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    approved_by: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class SendPlan(Base):
    """A sequence plan made on the Plan & Schedule screen (task 26): when email 1 goes, and after
    each email which email comes next, on which day, depending on what the lead did. `data` is
    checked by send_plan.clean_plan. The default plan is used for new email leads."""
    __tablename__ = "send_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    data: Mapped[Dict[str, Any]] = mapped_column(JSON)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class P0Template(Base):
    """An email the product manager wrote (P0 scheduled emails): draft until made live."""
    __tablename__ = "p0_template"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft|live|off
    created_by: Mapped[str] = mapped_column(String(80), default="")
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class P0ScheduleRow(Base):
    """The schedule: which template goes as email `email_no`, and when: `mode` "day" (`day` days after the
    first, in working hours), "now" (email 1 only: as soon as the lead starts) or "at" (the fixed moment `at`)."""
    __tablename__ = "p0_schedule_row"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_no: Mapped[int] = mapped_column(Integer, unique=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("p0_template.id"))
    day: Mapped[int] = mapped_column(Integer)  # 0 for "now" / "at" rows
    mode: Mapped[str] = mapped_column(String(5), default="day", server_default="day")
    at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class PlannedEmail(Base):
    """One email planned for one lead at a due time."""
    __tablename__ = "planned_email"
    __table_args__ = (
        UniqueConstraint("lead_id", "email_no"),
        Index("ix_planned_email_status_due", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("lead.id"))
    email_no: Mapped[int] = mapped_column(Integer)
    template_id: Mapped[int] = mapped_column(ForeignKey("p0_template.id"))
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(10), default="planned")  # planned|handed|sent|cancelled|skipped
    tool_step: Mapped[Optional[int]] = mapped_column(Integer)
    handed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    reason: Mapped[Optional[str]] = mapped_column(String(50))
    sent_subject: Mapped[Optional[str]] = mapped_column(Text)
    sent_text: Mapped[Optional[str]] = mapped_column(Text)
    exact: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # goes at due_at, no window
