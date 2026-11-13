"""Pure rules engine: (lead snapshot, event snapshot) -> ordered list of actions.

No DB, no I/O, no clock reads. Order of checks (rulings G, H):
  1. opt-outs, bounces and terminal-state bookings (apply in every state)
  2. terminal guard
  3. verdict != "human" for open / click / wa_read -> []
  4. per-kind handler
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional

from .actions import (
    Action, AddScore, Alert, BotReply, Classify, HandOff, PauseColleagues, RefreshNextStep,
    SendReply, SetFlag, SetNurtureUntil, SetState, SetStepSent, SetVariant, StopAllOutreach, Suppress,
    SuppressAllChannels, SwitchChannel, ToolStop, WaSendText,
)
from .channels import email_usable, whatsapp_usable
from .score import band, points_for
from .states import TERMINAL, LeadState

logger = logging.getLogger(__name__)

S = LeadState

WA_OPTOUT_KEYWORDS = frozenset({"stop", "unsubscribe", "opt out", "optout", "remove me"})
SOFT_BOUNCES_AS_HARD = 3
HINT_KINDS = frozenset({"open", "click", "wa_read"})

WA_NOT_ON_WHATSAPP = 131026
WA_UNDELIVERABLE_REASONS = {131026: "wa_undeliverable", 131049: "wa_marketing_limit"}
WA_RE_ENGAGEMENT = 131047
WA_USER_STOPPED_MARKETING = 131050

OUTREACH_STATES = frozenset({S.IN_SEQUENCE, S.PAUSED, S.NURTURE})
CONVERSATION_STATES = frozenset({S.ENGAGED, S.QUALIFYING, S.BOOKED})

FITS = frozenset({"high", "medium", "low"})
LOW_FIT_STATES = frozenset({S.IN_SEQUENCE, S.ENGAGED, S.QUALIFYING, S.NURTURE})
LOW_FIT_NURTURE = timedelta(days=90)


@dataclass(frozen=True)
class LeadSnapshot:
    id: int
    state: LeadState
    score: int
    step_sent: int
    active_channel: Optional[str]
    channel_switched: bool
    has_usable_email: bool
    has_wa_optin: bool
    email: Optional[str]
    phone: Optional[str]
    wa_human_owner: Optional[str]
    wa_window_open: bool
    flags: Mapping[str, Any]

    @classmethod
    def from_lead(cls, lead: Any, now: datetime) -> "LeadSnapshot":
        return cls(
            id=lead.id,
            state=LeadState(lead.state),
            score=lead.score or 0,
            step_sent=lead.step_sent or 0,
            active_channel=lead.active_channel,
            channel_switched=bool(lead.channel_switched),
            has_usable_email=email_usable(lead),
            has_wa_optin=whatsapp_usable(lead),
            email=lead.email,
            phone=lead.phone,
            wa_human_owner=lead.wa_human_owner,
            wa_window_open=lead.wa_window_until is not None and lead.wa_window_until > now,
            flags=MappingProxyType(dict(lead.variant_flags or {})),
        )


@dataclass(frozen=True)
class EventSnapshot:
    kind: str
    channel: str
    tag: Optional[str]
    verdict: Optional[str]
    step: Optional[int]
    occurred_at: datetime
    data: Mapping[str, Any]

    @classmethod
    def from_event(cls, event: Any) -> "EventSnapshot":
        return cls(
            kind=event.kind,
            channel=event.channel,
            tag=event.tag,
            verdict=event.verdict,
            step=event.step,
            occurred_at=event.occurred_at,
            data=MappingProxyType(dict(event.data or {})),
        )


# ------------------------------------------------------------------ helpers

def _set_state(lead: LeadSnapshot, to: LeadState, reason: str) -> List[Action]:
    """Never emit a same-state SetState (ruling F)."""
    return [] if lead.state == to else [SetState(to, reason)]


def _alert(lead: LeadSnapshot, kind: str, what: str) -> Alert:
    return Alert(kind, f"lead {lead.id}: {what}")


def _send(lead: LeadSnapshot, key: str) -> List[Action]:
    """Ruling E: send message `key` on the lead's active channel, or ask a human."""
    if lead.active_channel == "email":
        return [SendReply(key)]
    if lead.active_channel == "whatsapp" and lead.wa_window_open:
        return [WaSendText(key)]
    return [_alert(lead, "wa_human_needed", f"send '{key}' needs a human (WhatsApp window closed)")]


def _variant(lead: LeadSnapshot, flag: str) -> List[Action]:
    return [SetVariant(flag)] if lead.active_channel == "email" else []


def _wa_code(event: EventSnapshot) -> Optional[int]:
    try:
        return int(event.data.get("code"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _wa_text(event: EventSnapshot) -> str:
    return str(event.data.get("text") or "")


def _engage(lead: LeadSnapshot, reason: str) -> List[Action]:
    """First inbound from an outreach state: stop both channels and engage."""
    return [StopAllOutreach(), *_set_state(lead, S.ENGAGED, reason), Classify(), PauseColleagues()]


def _score(lead: LeadSnapshot, points: int, *, force_hot_alert: bool = False) -> List[Action]:
    """AddScore (if > 0) plus at most one hot_lead alert (ruling C)."""
    out: List[Action] = []
    if points > 0:
        out.append(AddScore(points))
    crossed = points > 0 and band(lead.score) != "hot" and band(lead.score + points) == "hot"
    alerted = bool(lead.flags.get("hot_alerted"))
    if (force_hot_alert or crossed) and not alerted:
        out.append(_alert(lead, "hot_lead", "hot lead"))
        out.append(SetFlag("hot_alerted", True))
    elif force_hot_alert:
        out.append(SetFlag("hot_alerted", True))
    return out


# ------------------------------------------------------------------ pre-guard (ruling G)

def _optout_reason(event: EventSnapshot) -> Optional[str]:
    if event.kind == "unsubscribe":
        return "unsubscribe"
    if event.kind == "wa_text" and _wa_text(event).strip().lower() in WA_OPTOUT_KEYWORDS:
        return "wa_optout"
    if event.kind == "wa_failed" and _wa_code(event) == WA_USER_STOPPED_MARKETING:
        return "wa_user_stopped_marketing"
    return None


def _optout(lead: LeadSnapshot, reason: str) -> List[Action]:
    if lead.state in TERMINAL:
        return [SuppressAllChannels(reason)]
    return [SuppressAllChannels(reason), StopAllOutreach(), SetState(S.SUPPRESSED, reason)]


def _hard_bounce(lead: LeadSnapshot) -> List[Action]:
    """Suppress the email always; state effects only for outreach states on email."""
    out: List[Action] = []
    if lead.email:
        out.append(Suppress(lead.email, "email", "hard_bounce"))
    if lead.state in TERMINAL:
        return out
    out.append(ToolStop())
    if lead.state not in OUTREACH_STATES or lead.active_channel == "whatsapp":
        # conversation continues / lead stays on WhatsApp
        return out
    if lead.has_wa_optin and not lead.channel_switched and lead.active_channel == "email":
        out.append(SwitchChannel("bounce"))
    else:
        out.append(SetState(S.SUPPRESSED, "hard_bounce"))
    return out


def _soft_bounce(lead: LeadSnapshot) -> List[Action]:
    n = int(lead.flags.get("soft_bounce_count", 0)) + 1
    if n >= SOFT_BOUNCES_AS_HARD:
        return _hard_bounce(lead)
    return [SetFlag("soft_bounce_count", n)]


def _pre_guard(lead: LeadSnapshot, event: EventSnapshot) -> Optional[List[Action]]:
    reason = _optout_reason(event)
    if reason is not None:
        return _optout(lead, reason)
    if event.kind == "bounce_hard":
        return _hard_bounce(lead)
    if event.kind == "bounce_soft":
        return _soft_bounce(lead)
    if event.kind == "booking_created" and lead.state in TERMINAL:
        return [_alert(lead, "booked", "booking from lead in terminal state")]
    if event.kind == "no_show" and lead.state == S.HANDED_OFF:
        # sales hosted the meeting and own the lead now: tell them, change nothing
        return [_alert(lead, "handoff", "Cal.com reported a no-show after handoff — follow up")]
    return None


# ------------------------------------------------------------------ per-kind handlers

LAST_EMAIL_STEP = 4


def _on_sent(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    """The tool reports its campaign step; after a mid-sequence re-enrolment (flags["step_offset"],
    see sequencer.enroll) our step is campaign step + offset, and the tool lead is stopped once our
    last step is out (the campaign's later steps only repeat it)."""
    if event.channel != "email" or event.step is None:
        return []
    try:
        offset = int(lead.flags.get("step_offset", 0))
    except (TypeError, ValueError):
        offset = 0
    step = event.step + offset
    if offset <= 0:
        return [SetStepSent(step), RefreshNextStep()]
    if step > LAST_EMAIL_STEP:
        return [ToolStop()]
    out: List[Action] = [SetStepSent(step), RefreshNextStep()]
    if step == LAST_EMAIL_STEP:
        out.append(ToolStop())
    return out


def _on_open(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    """Opens are unreliable (Apple auto-opens, Outlook blocks images): count them for
    reporting only. They never change score, content, subject, timing or routing."""
    opens = int(lead.flags.get("human_opens", 0))
    return [SetFlag("human_opens", opens + 1)]


def _on_click(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    tag = event.tag
    pts = points_for("click", tag, 0, False)
    if tag == "pricing":
        out = _score(lead, pts, force_hot_alert=True)
        out += _variant(lead, "booking_slots")
        if lead.active_channel == "email":
            out.append(SendReply("booking_slots"))
        elif lead.active_channel == "whatsapp" and lead.wa_window_open:
            out.append(WaSendText("booking_slots"))
        # WA window closed: the hot_lead alert / score is enough (ruling E)
        return out
    out = _score(lead, pts)
    if tag in ("case_study", "product"):
        out += _variant(lead, tag)
    return out


def _on_reply(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state in OUTREACH_STATES:
        return _engage(lead, "reply")
    if lead.state in CONVERSATION_STATES:
        return [Classify()]
    return []


def _on_wa_read(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    scored = bool(lead.flags.get("wa_read_scored"))
    pts = points_for("wa_read", None, 0, scored)
    if pts <= 0:
        return []
    return [*_score(lead, pts), SetFlag("wa_read_scored", True)]


def _on_wa_failed(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state != S.IN_SEQUENCE or lead.active_channel != "whatsapp":
        return []
    code = _wa_code(event)
    if code == WA_RE_ENGAGEMENT:
        return []
    if code in WA_UNDELIVERABLE_REASONS:
        reason = WA_UNDELIVERABLE_REASONS[code]
        if lead.has_usable_email and not lead.channel_switched:
            return [SwitchChannel(reason)]
        if code == WA_NOT_ON_WHATSAPP:
            out: List[Action] = []
            if lead.phone:
                out.append(Suppress(lead.phone, "phone", reason))
            return out + [SetState(S.CLOSED, "unreachable")]
        return [SetState(S.NURTURE, reason)]
    return [_alert(lead, "wa_send_failed", f"WhatsApp send failed (code {code})")]


def _book_demo(lead: LeadSnapshot) -> List[Action]:
    send = WaSendText("booking_link")
    if lead.state in (S.IN_SEQUENCE, S.NURTURE):
        return [StopAllOutreach(), SetState(S.QUALIFYING, "book_demo"), send, PauseColleagues()]
    if lead.state == S.PAUSED:
        return [StopAllOutreach(), SetState(S.ENGAGED, "book_demo"),
                SetState(S.QUALIFYING, "book_demo"), send, PauseColleagues()]
    if lead.state == S.ENGAGED:
        return [SetState(S.QUALIFYING, "book_demo"), send]
    if lead.state in (S.QUALIFYING, S.BOOKED):
        return [send]
    return []


def _ask_question(lead: LeadSnapshot) -> List[Action]:
    send = WaSendText("ask_away")
    if lead.state in OUTREACH_STATES:
        return [StopAllOutreach(), SetState(S.ENGAGED, "ask_question"), send, PauseColleagues()]
    return [send]


def _on_wa_button(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    payload = event.data.get("payload")
    if payload == "BOOK_DEMO":
        return _book_demo(lead)
    if payload == "ASK_QUESTION":
        return _ask_question(lead)
    return [_alert(lead, "unsure", "unknown WhatsApp button payload")]


def _on_wa_text(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state in OUTREACH_STATES:
        return _engage(lead, "wa_text")
    if lead.state in CONVERSATION_STATES:
        return [] if lead.wa_human_owner else [BotReply()]
    return []


def _booking_flags(event: EventSnapshot) -> List[Action]:
    """Remember the current booking (uid / end) so a cancel of an older booking is ignored."""
    out: List[Action] = []
    end, uid = event.data.get("end"), event.data.get("uid")
    if end:
        out.append(SetFlag("booking_end", end))
    if uid:
        out.append(SetFlag("booking_uid", uid))
    return out


def _on_booking_created(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    flags = _booking_flags(event)
    tail: List[Action] = [*flags, _alert(lead, "booked", "meeting booked"), PauseColleagues()]
    if lead.state in (S.IN_SEQUENCE, S.ENGAGED, S.NURTURE, S.QUALIFYING):
        return [StopAllOutreach(), SetState(S.BOOKED, "booking_created"), *tail]
    if lead.state == S.PAUSED:
        return [StopAllOutreach(), SetState(S.ENGAGED, "booking_created"),
                SetState(S.BOOKED, "booking_created"), *tail]
    if lead.state == S.BOOKED:
        return flags  # a second booking: track the newest one
    return []


def _on_booking_rescheduled(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state == S.QUALIFYING:
        # no current booking (after a no-show or cancel): Cal.com's own reschedule link rebooks
        return [StopAllOutreach(), SetState(S.BOOKED, "booking_rescheduled"), *_booking_flags(event),
                _alert(lead, "booked", "meeting rebooked"), PauseColleagues()]
    return _booking_flags(event)


def _stale_booking(lead: LeadSnapshot, event: EventSnapshot) -> bool:
    """The event is about an older booking than the one the lead holds now."""
    current, uid = lead.flags.get("booking_uid"), event.data.get("uid")
    return bool(current and uid and current != uid)


def _on_booking_cancelled(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state != S.BOOKED or _stale_booking(lead, event):
        return []  # an older booking was cancelled; the current one stands
    return [SetState(S.QUALIFYING, "booking_cancelled"), _alert(lead, "booked", "booking cancelled")]


def _on_form_submitted(lead: LeadSnapshot, event: EventSnapshot, now: datetime) -> List[Action]:
    fit = event.data.get("fit")
    if fit not in FITS:
        return []
    out: List[Action] = [SetFlag("fit", fit)]
    if fit != "low" or lead.state not in LOW_FIT_STATES:
        return out
    return [*out, StopAllOutreach(), *_set_state(lead, S.NURTURE, "low_fit"),
            SetNurtureUntil(now + LOW_FIT_NURTURE)]


def _on_no_show(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    # A no-show can only follow a booking; other states have no legal target path.
    if lead.state != S.BOOKED or _stale_booking(lead, event):
        return []
    alert = _alert(lead, "no_show", "lead did not attend the meeting")
    if int(lead.flags.get("no_show_count", 0)) == 0:
        return [SetFlag("no_show_count", 1), SetState(S.QUALIFYING, "no_show"), alert,
                *_send(lead, "reschedule")]
    return [SetFlag("no_show_count", 2), SetState(S.NURTURE, "no_show_x2"), alert]


def _on_meeting_ended(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    if lead.state != S.BOOKED or _stale_booking(lead, event):
        return []
    return [SetState(S.HANDED_OFF, "attended"), HandOff()]


BOOKING_NUDGE_STATES = frozenset({S.IN_SEQUENCE, S.ENGAGED, S.QUALIFYING, S.NURTURE})


def _on_booking_click_not_booked(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    """Synthetic (worker): a human booking click 30 min ago and still no booking. Offer slots on the
    active channel, or ask a person when it cannot be sent (WhatsApp window closed). A paused lead
    is not messaged; a booked one needs nothing."""
    if lead.state not in BOOKING_NUDGE_STATES:
        return []
    return _send(lead, "booking_slots")


def _nothing(lead: LeadSnapshot, event: EventSnapshot) -> List[Action]:
    return []


Handler = Callable[[LeadSnapshot, EventSnapshot], List[Action]]

_HANDLERS: Dict[str, Handler] = {
    "sent": _on_sent,
    "open": _on_open,
    "click": _on_click,
    "reply": _on_reply,
    "wa_sent": _nothing,
    "wa_delivered": _nothing,
    "wa_read": _on_wa_read,
    "wa_failed": _on_wa_failed,
    "wa_button": _on_wa_button,
    "wa_text": _on_wa_text,
    "booking_created": _on_booking_created,
    "no_show": _on_no_show,
    "meeting_ended": _on_meeting_ended,
    "booking_rescheduled": _on_booking_rescheduled,
    "booking_cancelled": _on_booking_cancelled,
    "booking_click_not_booked": _on_booking_click_not_booked,
}

_TIMED_HANDLERS: Dict[str, Callable[[LeadSnapshot, EventSnapshot, datetime], List[Action]]] = {
    "form_submitted": _on_form_submitted,
}


def decide(lead: LeadSnapshot, event: EventSnapshot, now: datetime) -> List[Action]:
    """Return the ordered actions for `event` on `lead`. Pure; `now` is only used to compute
    future dates (nurture_until); other time-dependent facts are precomputed into the snapshots."""
    pre = _pre_guard(lead, event)
    if pre is not None:
        return pre
    if lead.state in TERMINAL:
        return []
    if event.kind in HINT_KINDS and event.verdict != "human":
        return []
    timed = _TIMED_HANDLERS.get(event.kind)
    if timed is not None:
        return timed(lead, event, now)
    handler = _HANDLERS.get(event.kind)
    if handler is None:
        logger.debug("lead %s: no rule for event kind %s", lead.id, event.kind)
        return []
    return handler(lead, event)
