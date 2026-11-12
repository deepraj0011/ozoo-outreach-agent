from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .models import Lead

logger = logging.getLogger(__name__)

SCORE_POINTS = {
    "homepage": 2,
    "case_study": 5,
    "product": 5,
    "pricing": 10,
    "booking": 10,
    "unsubscribe": 0,
}
OPEN_MAX_COUNTED = 3
OPEN_POINTS = 1
OPEN_BONUS_AT_MAX = 2
WA_READ_POINTS = 1
DECAY_AFTER = timedelta(days=30)
COLD_MAX = 4
WARM_MAX = 14


def apply_points(lead: Lead, event: Any) -> int:
    """Add points for a human event to lead.score; returns the points added."""
    if event.verdict != "human":
        return 0
    flags = lead.variant_flags
    if event.kind == "open":
        n = int(flags.get("human_opens", 0)) + 1
        flags["human_opens"] = n
        pts = OPEN_POINTS if n <= OPEN_MAX_COUNTED else 0
        if n == OPEN_MAX_COUNTED:
            pts += OPEN_BONUS_AT_MAX
    elif event.kind == "click":
        pts = SCORE_POINTS.get(event.tag or "", 0)
    elif event.kind == "wa_read":
        pts = 0 if flags.get("wa_read_scored") else WA_READ_POINTS
        flags["wa_read_scored"] = True
    else:
        return 0
    lead.score = (lead.score or 0) + pts
    lead.last_human_event_at = event.occurred_at
    return pts


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
