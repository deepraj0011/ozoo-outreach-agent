from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LeadState(str, Enum):
    INTAKE = "intake"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"
    IN_SEQUENCE = "in_sequence"
    PAUSED = "paused"
    ENGAGED = "engaged"
    QUALIFYING = "qualifying"
    BOOKED = "booked"
    HANDED_OFF = "handed_off"
    NURTURE = "nurture"
    CLOSED = "closed"
    ARCHIVED = "archived"


S = LeadState

TERMINAL = frozenset({S.REJECTED, S.SUPPRESSED, S.CLOSED, S.HANDED_OFF, S.ARCHIVED})
TERMINAL_VALUES = frozenset(s.value for s in TERMINAL)  # for SQL filters and string comparisons

_PAIRS = [
    # section 11 diagram
    (S.INTAKE, S.REJECTED), (S.INTAKE, S.SUPPRESSED), (S.INTAKE, S.IN_SEQUENCE),
    (S.IN_SEQUENCE, S.ENGAGED), (S.IN_SEQUENCE, S.NURTURE),
    (S.IN_SEQUENCE, S.PAUSED), (S.IN_SEQUENCE, S.SUPPRESSED),
    (S.PAUSED, S.IN_SEQUENCE),
    (S.ENGAGED, S.QUALIFYING), (S.ENGAGED, S.NURTURE), (S.ENGAGED, S.CLOSED),
    (S.QUALIFYING, S.BOOKED), (S.QUALIFYING, S.NURTURE),
    (S.BOOKED, S.HANDED_OFF), (S.BOOKED, S.QUALIFYING),
    (S.HANDED_OFF, S.NURTURE),
    (S.NURTURE, S.ENGAGED), (S.NURTURE, S.ARCHIVED),
    # additions
    (S.INTAKE, S.PAUSED),
    (S.PAUSED, S.ENGAGED), (S.ENGAGED, S.PAUSED), (S.PAUSED, S.SUPPRESSED),
    (S.IN_SEQUENCE, S.BOOKED), (S.NURTURE, S.BOOKED), (S.ENGAGED, S.BOOKED),
    (S.IN_SEQUENCE, S.QUALIFYING),
    (S.IN_SEQUENCE, S.CLOSED),
    (S.BOOKED, S.NURTURE), (S.NURTURE, S.QUALIFYING),
]
# every non-terminal state may be suppressed
_PAIRS += [(s, S.SUPPRESSED) for s in LeadState if s not in TERMINAL]


def _build() -> Dict[LeadState, FrozenSet[LeadState]]:
    out: Dict[LeadState, set] = {s: set() for s in LeadState}
    for a, b in _PAIRS:
        out[a].add(b)
    return {s: frozenset(v) for s, v in out.items()}


ALLOWED: Dict[LeadState, FrozenSet[LeadState]] = _build()


class IllegalTransition(Exception):
    pass


def transition(session: Session, lead, to: LeadState, reason: str, now: datetime) -> None:
    """Move lead to `to`, recording a state_change Event. Caller commits."""
    from .models import Event

    frm = LeadState(lead.state)
    to = LeadState(to)
    if to not in ALLOWED[frm]:
        raise IllegalTransition(f"{frm.value} -> {to.value}")
    session.add(
        Event(
            lead_id=lead.id,
            channel="system",
            kind="state_change",
            occurred_at=now,
            received_at=now,
            verdict="n/a",
            processed=True,
            data={"from": frm.value, "to": to.value, "reason": reason},
        )
    )
    lead.state = to.value
    session.flush()
    logger.info("lead %s: %s -> %s", lead.id, frm.value, to.value)
