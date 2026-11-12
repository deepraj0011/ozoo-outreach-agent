"""Actions produced by the rules engine (rules.decide) and their executor, execute()."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple, Union

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .intake import FREE_MAIL_DOMAINS
from .models import Alert as AlertRow
from .models import Event, Lead, Suppression
from .states import IllegalTransition, LeadState, transition

if TYPE_CHECKING:  # pragma: no cover
    from .emailtool.base import EmailTool

ALERT_KINDS = frozenset({
    "hot_lead", "hot_no_reply", "question", "unsure", "unmatched_reply", "unmatched_inbound",
    "unmatched_booking", "mailbox_paused", "booked", "no_show", "wa_human_needed",
    "wa_sla_missed", "wa_send_failed", "handoff", "integration_error",
})


# --- state and suppression
@dataclass(frozen=True)
class SetState:
    to: LeadState
    reason: str = ""


@dataclass(frozen=True)
class Suppress:
    value: str
    kind: str  # email|phone
    reason: str


@dataclass(frozen=True)
class SuppressAllChannels:
    """Suppress both the lead's email and phone."""
    reason: str


# --- email tool
@dataclass(frozen=True)
class ToolStop:
    pass


@dataclass(frozen=True)
class ToolPause:
    until: datetime


@dataclass(frozen=True)
class ToolResume:
    pass


# --- WhatsApp sends
@dataclass(frozen=True)
class WaCancelReminder:
    pass


@dataclass(frozen=True)
class WaSendText:
    """Free-form message; only valid while the 24 h window is open."""
    text_key: str


@dataclass(frozen=True)
class WaSendTemplate:
    name: str


# --- routing / scoring
@dataclass(frozen=True)
class SwitchChannel:
    reason: str


@dataclass(frozen=True)
class SetVariant:
    flag: str


@dataclass(frozen=True)
class AddScore:
    points: int


@dataclass(frozen=True)
class SetFlag:
    key: str
    value: Any


@dataclass(frozen=True)
class SetStepSent:
    step: int


@dataclass(frozen=True)
class RefreshNextStep:
    pass


@dataclass(frozen=True)
class Alert:
    kind: str
    text: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if self.kind not in ALERT_KINDS:
            raise ValueError(f"unknown alert kind: {self.kind}")


# --- follow-ups
@dataclass(frozen=True)
class Classify:
    pass


@dataclass(frozen=True)
class BotReply:
    pass


@dataclass(frozen=True)
class SendReply:
    template: str


@dataclass(frozen=True)
class PauseColleagues:
    pass


@dataclass(frozen=True)
class HandOff:
    pass


@dataclass(frozen=True)
class StopAllOutreach:
    """Composite: stop the email tool and cancel any pending WhatsApp reminder."""

    def expand(self) -> Tuple[ToolStop, WaCancelReminder]:
        return (ToolStop(), WaCancelReminder())


Action = Union[
    SetState, Suppress, SuppressAllChannels, ToolStop, ToolPause, ToolResume,
    WaCancelReminder, WaSendText, WaSendTemplate, SwitchChannel, SetVariant, AddScore,
    SetFlag, SetStepSent, RefreshNextStep, Alert, Classify, BotReply, SendReply,
    PauseColleagues, HandOff, StopAllOutreach,
]


def flatten(actions: Iterable[Action]) -> List[Action]:
    """Expand composite actions (StopAllOutreach) into their primitive parts, in order."""
    out: List[Action] = []
    for a in actions:
        if isinstance(a, StopAllOutreach):
            out.extend(a.expand())
        else:
            out.append(a)
    return out


# ====================================================================== executor
# Ports below are satisfied by later tasks (WhatsApp client T8, outreach/sequencer T12,
# classifier T9, chatbot T10, handoff T11); tests use the fakes in tests/fakes.py.

logger = logging.getLogger(__name__)

MAX_FOLLOWUP_DEPTH = 2


class WaPort(Protocol):
    def send_text(self, session: Session, lead: Lead, key: str, now: datetime) -> Any: ...

    def send_template(self, session: Session, lead: Lead, name: str, now: datetime) -> Any: ...


class OutreachPort(Protocol):
    def switch(self, session: Session, lead: Lead, reason: str, now: datetime) -> Any: ...

    def refresh_next_step(self, session: Session, lead: Lead) -> Any: ...

    def send_email_text(self, session: Session, lead: Lead, key: str, now: datetime) -> Any: ...


class Alerter(Protocol):
    def send(self, kind: str, text: str, lead: Optional[Lead] = None,
             session: Optional[Session] = None) -> None: ...


@dataclass
class Deps:
    tool: "EmailTool"
    wa: WaPort
    alerter: Alerter
    outreach: OutreachPort
    classify: Optional[Callable[[Session, Lead, Event, datetime], List[Action]]] = None
    bot_reply: Optional[Callable[[Session, Lead, datetime], List[Action]]] = None
    handoff: Optional[Callable[[Session, Lead, datetime], None]] = None


_FAILED = object()


class _Run:
    """One execute() call: shared context for the per-action handlers."""

    def __init__(self, session: Session, lead: Lead, deps: Deps, now: datetime,
                 event: Optional[Event], depth: int) -> None:
        self.session, self.lead, self.deps, self.now = session, lead, deps, now
        self.event, self.depth = event, depth

    # ---------------------------------------------------------------- helpers
    @property
    def event_time(self) -> datetime:
        return self.event.occurred_at if self.event is not None else self.now

    def flags(self, lead: Optional[Lead] = None) -> Dict[str, Any]:
        ld = lead or self.lead
        if ld.variant_flags is None:
            ld.variant_flags = {}
        return ld.variant_flags

    def integration_error(self, what: str, lead: Optional[Lead] = None) -> None:
        """Recorded straight to the DB (not via Slack, which may be what is failing)."""
        ld = lead or self.lead
        self.session.add(AlertRow(lead_id=ld.id, kind="integration_error",
                                  text=f"lead {ld.id}: {what}", created_at=self.now))
        self.session.flush()

    def external(self, what: str, fn: Callable[..., Any], *args: Any,
                 lead: Optional[Lead] = None) -> Any:
        """Call a port inside a savepoint; on any failure log (no PII), alert, continue."""
        ld = lead or self.lead
        try:
            with self.session.begin_nested():
                return fn(*args)
        except Exception as exc:  # noqa: BLE001 - one failing integration must not stop the rest
            logger.warning("lead %s: %s failed: %s", ld.id, what, type(exc).__name__)
            self.integration_error(f"{what} failed: {type(exc).__name__}", ld)
            return _FAILED

    def alert(self, kind: str, text: str) -> None:
        self.external("alerter.send", self.deps.alerter.send, kind, text, self.lead, self.session)

    def set_state(self, lead: Lead, to: Any, reason: str) -> bool:
        frm = lead.state
        try:
            transition(self.session, lead, to, reason, self.now)
            return True
        except IllegalTransition:
            to_v = getattr(to, "value", to)
            logger.warning("lead %s: illegal transition %s -> %s (%s)", lead.id, frm, to_v, reason)
            self.integration_error(f"illegal transition {frm} -> {to_v} ({reason})", lead)
            return False

    def suppress(self, value: Optional[str], kind: str, reason: str) -> None:
        if not value:
            return
        exists = self.session.scalar(select(Suppression.id).where(Suppression.value == value).limit(1))
        if exists is None:
            try:
                with self.session.begin_nested():
                    self.session.add(Suppression(value=value, kind=kind, reason=reason, created_at=self.now))
            except IntegrityError:
                pass  # concurrent insert of the same value: already suppressed
        # ruling 11: the channel becomes unusable on the lead itself
        if kind == "email" and value == self.lead.email:
            self.lead.verify_status = "invalid"
        elif kind == "phone" and value == self.lead.phone:
            self.lead.consent_whatsapp = False

    def followup(self, what: str, fn: Optional[Callable[..., List[Action]]], *args: Any) -> None:
        if fn is None:
            self.alert("unsure", f"lead {self.lead.id}: {what} not available; please handle manually")
            return
        if self.depth >= MAX_FOLLOWUP_DEPTH:
            logger.warning("lead %s: %s skipped, follow-up depth %s reached", self.lead.id, what, self.depth)
            self.integration_error(f"{what} skipped: follow-up depth limit")
            return
        result = self.external(what, fn, *args)
        if result is not _FAILED and result:
            execute(self.session, self.lead, result, self.deps, self.now, self.event, _depth=self.depth + 1)

    # ---------------------------------------------------------------- composite effects
    def pause_colleagues(self) -> None:
        lead = self.lead
        domain = lead.domain or (lead.email.split("@", 1)[1] if lead.email and "@" in lead.email else None)
        if not domain or domain.lower() in FREE_MAIL_DOMAINS:
            return
        colleagues = self.session.scalars(
            select(Lead).where(Lead.domain == domain, Lead.id != lead.id,
                               Lead.state == LeadState.IN_SEQUENCE.value).order_by(Lead.id)
        ).all()
        for c in colleagues:
            if not self.set_state(c, LeadState.PAUSED, "account"):
                continue
            c.pause_reason = "account"
            self.flags(c)["wa_reminder_cancelled"] = True
            if c.tool_lead_id:
                self.external("tool.pause", self.deps.tool.pause, c.tool_lead_id, lead=c)
            logger.info("lead %s paused: colleague lead %s engaged", c.id, lead.id)

    # ---------------------------------------------------------------- dispatch
    def apply(self, a: Action) -> None:  # noqa: C901 - flat dispatch table
        lead, deps, s = self.lead, self.deps, self.session
        if isinstance(a, SetState):
            self.set_state(lead, a.to, a.reason)
        elif isinstance(a, Suppress):
            self.suppress(a.value, a.kind, a.reason)
        elif isinstance(a, SuppressAllChannels):
            self.suppress(lead.email, "email", a.reason)
            self.suppress(lead.phone, "phone", a.reason)
        elif isinstance(a, ToolStop):
            if lead.tool_lead_id:
                self.external("tool.stop", deps.tool.stop, lead.tool_lead_id)
        elif isinstance(a, ToolPause):
            lead.pause_until = a.until
            if lead.tool_lead_id:
                self.external("tool.pause", deps.tool.pause, lead.tool_lead_id)
        elif isinstance(a, ToolResume):
            if lead.tool_lead_id:
                self.external("tool.resume", deps.tool.resume, lead.tool_lead_id)
        elif isinstance(a, WaCancelReminder):
            self.flags()["wa_reminder_cancelled"] = True
        elif isinstance(a, WaSendText):
            self.external("wa.send_text", deps.wa.send_text, s, lead, a.text_key, self.now)
        elif isinstance(a, WaSendTemplate):
            self.external("wa.send_template", deps.wa.send_template, s, lead, a.name, self.now)
        elif isinstance(a, SendReply):
            self.external("outreach.send_email_text", deps.outreach.send_email_text, s, lead, a.template, self.now)
        elif isinstance(a, SwitchChannel):
            self.external("outreach.switch", deps.outreach.switch, s, lead, a.reason, self.now)
        elif isinstance(a, RefreshNextStep):
            self.external("outreach.refresh_next_step", deps.outreach.refresh_next_step, s, lead)
        elif isinstance(a, SetVariant):
            flags = self.flags()
            flags["variants"] = sorted(set(flags.get("variants") or []) | {a.flag})
            self.external("outreach.refresh_next_step", deps.outreach.refresh_next_step, s, lead)
        elif isinstance(a, AddScore):
            lead.score = (lead.score or 0) + a.points
            lead.last_human_event_at = self.event_time
        elif isinstance(a, SetFlag):
            self.flags()[a.key] = a.value
        elif isinstance(a, SetStepSent):
            lead.step_sent = max(lead.step_sent or 0, a.step)
            lead.last_sent_at = self.event_time
        elif isinstance(a, Alert):
            self.alert(a.kind, a.text)
        elif isinstance(a, PauseColleagues):
            self.pause_colleagues()
        elif isinstance(a, Classify):
            if self.event is None:
                self.alert("unsure", f"lead {lead.id}: classify requested without an event")
            else:
                self.followup("classify", deps.classify, s, lead, self.event, self.now)
        elif isinstance(a, BotReply):
            self.followup("bot_reply", deps.bot_reply, s, lead, self.now)
        elif isinstance(a, HandOff):
            if deps.handoff is None:
                self.alert("handoff", f"lead {lead.id}: hand off to sales")
            else:
                self.external("handoff", deps.handoff, s, lead, self.now)
        else:  # pragma: no cover - new action type without an executor branch
            logger.error("lead %s: no executor for %s", lead.id, type(a).__name__)
            self.integration_error(f"no executor for {type(a).__name__}")


def execute(session: Session, lead: Lead, actions: Iterable[Action], deps: Deps, now: datetime,
            event: Optional[Event] = None, *, _depth: int = 0) -> None:
    """Apply `actions` to `lead` in order. Never raises for a failing integration or an
    illegal transition (both become integration_error alerts). Caller commits."""
    run = _Run(session, lead, deps, now, event, _depth)
    for action in flatten(actions):
        run.apply(action)
    session.flush()
