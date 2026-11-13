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
    "pricing": 10,
    "booking": 10,
    "unsubscribe": 0,
}
WA_READ_POINTS = 1
DECAY_AFTER = timedelta(days=30)
COLD_MAX = 4
WARM_MAX = 14


def points_for(kind: str, tag: Optional[str], human_opens_so_far: int, wa_read_scored: bool) -> int:
    """Pure: points a human event of `kind`/`tag` is worth given the lead's counters.
    Email opens are worth 0: they are unreliable and never drive decisions."""
    if kind == "click":
        return SCORE_POINTS.get(tag or "", 0)
    if kind == "wa_read":
        return 0 if wa_read_scored else WA_READ_POINTS
    return 0


def apply_points(lead: Lead, event: Any) -> int:
    """Add points for a human event to lead.score; returns the points added."""
    if event.verdict != "human" or event.kind not in ("open", "click", "wa_read"):
        return 0
    flags = lead.variant_flags
    opens = int(flags.get("human_opens", 0))
    pts = points_for(event.kind, event.tag, opens, bool(flags.get("wa_read_scored")))
    if event.kind == "open":
        flags["human_opens"] = opens + 1
    elif event.kind == "wa_read":
        flags["wa_read_scored"] = True
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
