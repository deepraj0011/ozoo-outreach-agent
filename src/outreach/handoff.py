"""WhatsApp human takeover (plan section 7).

start_takeover() mutes the bot (variant_flags["takeover_at"]), alerts Slack with the recent
transcript and the /wa usage, and tells the lead a person will reply. A person then answers with
`/wa <lead_id> <message>` and gives the chat back with `/wa-done <lead_id>` (see app.py).
Flushes only; the caller commits. Message text goes to the Alert row / Slack, never to logs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from .actions import record_integration_error
from .models import ChatMessage, Lead
from .whatsapp.sender import window_open

if TYPE_CHECKING:  # pragma: no cover
    from .actions import Deps

logger = logging.getLogger(__name__)

TRANSCRIPT_MESSAGES = 10
TRANSCRIPT_LINE_MAX = 300


def transcript(session: Session, lead: Lead) -> List[str]:
    """The last TRANSCRIPT_MESSAGES chat lines, oldest first, as "lead:/bot:/human: <text>"."""
    session.flush()
    rows = list(session.scalars(
        select(ChatMessage).where(ChatMessage.lead_id == lead.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(TRANSCRIPT_MESSAGES)))
    rows.reverse()
    return [f"{m.author}: {(m.text or '').strip()[:TRANSCRIPT_LINE_MAX]}" for m in rows]


def start_takeover(session: Session, lead: Lead, reason: str, deps: "Deps", now: datetime) -> None:
    flags = dict(lead.variant_flags or {})
    flags["takeover_at"] = now.isoformat()
    flags["takeover_reason"] = reason
    flags.pop("takeover_released", None)
    lead.variant_flags = flags

    lines = transcript(session, lead) or ["(no messages)"]
    text = "\n".join([
        f"lead {lead.id}: WhatsApp chat needs a person (reason: {reason}). The bot is paused.",
        "Recent messages:",
        *lines,
        f"Reply: /wa {lead.id} <message>",
        f"Give back to the bot: /wa-done {lead.id}",
    ])
    deps.alerter.send("wa_human_needed", text, lead, now, session)
    logger.info("lead %s: WhatsApp takeover started (%s)", lead.id, reason)

    if not window_open(lead, now):
        return
    try:
        with session.begin_nested():
            deps.wa.send_text(session, lead, "handoff_notice", now)
    except Exception as exc:  # noqa: BLE001 - the alert is what matters; the notice is a courtesy
        logger.warning("lead %s: handoff notice failed: %s", lead.id, type(exc).__name__)
        record_integration_error(session, lead.id, f"lead {lead.id}: handoff notice failed: "
                                                   f"{type(exc).__name__}", now)
