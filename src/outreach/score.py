from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from .models import Lead

logger = logging.getLogger(__name__)

SCORE_POINTS = {
    "homepage": 2,
    "case_study": 5,
    "product": 5,
    "voice": 5,
    "chat": 5,
    "pricing": 10,
    "trial": 10,
    "booking": 10,
    "unsubscribe": 0,
}
WA_READ_POINTS = 1
# an engaged visit (outreach.visits: a person read the page) is worth as much as the click, again:
# it confirms the click was a person, not a security scanner
VISIT_POINTS = {
    "homepage": 3,
    "case_study": 5,
    "product": 5,
    "voice": 5,
    "chat": 5,
    "pricing": 10,
}
DECAY_AFTER = timedelta(days=30)
COLD_MAX = 4
WARM_MAX = 14


def points_for(kind: str, tag: Optional[str], human_opens_so_far: int, wa_read_scored: bool) -> int:
    """Pure: points a human event of `kind`/`tag` is worth given the lead's counters.
    Email opens are worth 0: they are unreliable and never drive decisions."""
    if kind == "click":
        return SCORE_POINTS.get(tag or "", 0)
    if kind == "visit":
        return VISIT_POINTS.get(tag or "", 0)
    if kind == "wa_read":
        return 0 if wa_read_scored else WA_READ_POINTS
    return 0


def decay(lead: Lead, now: datetime) -> bool:
    """Halve the score if 30 days passed since the last human event / last decay."""
    anchor = lead.last_human_event_at or lead.created_at
    raw = lead.variant_flags.get("last_decay_at")
    if raw:
        try:
            last = datetime.fromisoformat(raw)
            if anchor is None or last > anchor:
                anchor = last
        except (ValueError, TypeError):
            pass
    if anchor is None or (lead.score or 0) <= 0 or now - anchor < DECAY_AFTER:
        return False
    lead.score //= 2
    lead.variant_flags["last_decay_at"] = now.isoformat()
    return True


def band(score: int) -> str:
    if score <= COLD_MAX:
        return "cold"
    if score <= WARM_MAX:
        return "warm"
    return "hot"
