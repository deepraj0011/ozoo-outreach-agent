"""Actions produced by the rules engine (rules.decide). Data only; the executor is Task 6."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Tuple, Union

from .states import LeadState

ALERT_KINDS = frozenset({
    "hot_lead", "hot_no_reply", "question", "unsure", "unmatched_reply", "unmatched_inbound",
    "unmatched_booking", "mailbox_paused", "booked", "no_show", "wa_human_needed",
    "wa_sla_missed", "wa_send_failed", "handoff",
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
