"""Mailbox health (plan section 5j): bounce rate over the last 7 days, from our own events.

Our events do not record the sending mailbox (Instantly's webhook payload is mapped without its
`eaccount`), so the rate is judged per campaign (Event.data["campaign_id"]); events without a
campaign id are judged together as "unknown". A campaign with more than 2 % bounces (hard + soft)
on at least 50 sends is paused when the tool can (`pause_campaign`), else sales is alerted to pause
it by hand. Fewer than 50 sends: not judged. Called daily by the worker.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
MIN_SENDS = 50
MAX_BOUNCE_RATE = 0.02
BOUNCE_KINDS = ("bounce_hard", "bounce_soft")
UNKNOWN = "unknown"


def _campaign(data: Any) -> str:
    cid = (data or {}).get("campaign_id") if isinstance(data, dict) else None
    return str(cid)[:100] if cid else UNKNOWN


def bounce_stats(session: Session, now: datetime) -> dict:
    """{campaign_id: (sends, bounces)} over the last WINDOW."""
    rows = session.execute(
        select(Event.kind, Event.data).where(
            Event.channel == "email", Event.kind.in_(("sent", *BOUNCE_KINDS)), Event.occurred_at >= now - WINDOW,
            Event.occurred_at <= now)
    ).all()
    sends: Counter = Counter()
    bounces: Counter = Counter()
    for kind, data in rows:
        (sends if kind == "sent" else bounces)[_campaign(data)] += 1
    return {cid: (sends[cid], bounces[cid]) for cid in set(sends) | set(bounces)}


def _pause(tool: Any, campaign_id: str) -> bool:
    pause = getattr(tool, "pause_campaign", None)
    if pause is None or campaign_id == UNKNOWN:
        return False
    try:
        pause(campaign_id)
        return True
    except Exception as exc:  # noqa: BLE001 - unsupported / failing: a person pauses it
        logger.warning("campaign pause failed: %s", type(exc).__name__)
        return False


def check_mailboxes(session: Session, tool: Any, alerter: Any, now: datetime) -> List[str]:
    """Flag campaigns over the bounce limit; returns their ids (sorted)."""
    flagged: List[str] = []
    for cid, (sent, bounced) in sorted(bounce_stats(session, now).items()):
        if sent < MIN_SENDS:
            continue
        rate = bounced / sent
        if rate <= MAX_BOUNCE_RATE:
            continue
        flagged.append(cid)
        paused = _pause(tool, cid)
        what = ("paused automatically" if paused
                else "NOT paused (the tool cannot do it) — please pause it manually in the email tool")
        text = (f"campaign {cid}: bounce rate {rate * 100:.1f}% ({bounced} of {sent} sends, last 7 days) "
                f"is over {MAX_BOUNCE_RATE * 100:.0f}%; {what}")
        logger.warning("campaign %s bounce rate %.1f%% over %s sends; paused=%s", cid, rate * 100, sent, paused)
        alerter.send("mailbox_paused", text, None, now, session)
    return flagged
