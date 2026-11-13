"""TeamsAlerter: every alert is an Alert row first; the Microsoft Teams post (an Adaptive Card
sent to a Teams Workflows webhook) happens only after the caller's transaction commits (never
inside it). Unposted rows keep posted_at NULL for the worker to retry.

The webhook URL carries a secret (its sig= query parameter): it is never logged, and neither are
exception messages from the post (they may contain the URL), only the exception type / status."""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import httpx
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, SessionTransaction, object_session

from .http import make_client, request_with_retry
from .models import Alert as AlertRow

logger = logging.getLogger(__name__)

_PENDING = "alert_pending"
_HOOKED = "alert_hooked"

BLOCK_MAX = 1000  # characters per card TextBlock (transcript lines are already cut at 300)
BODY_BLOCKS_MAX = 50  # third-party TextBlocks per card
CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Adaptive Card markdown has no reliable backslash escape: swap markdown-active characters for
# lookalikes so lead / model text cannot make links, emphasis, headings, code or <at> mentions.
_LOOKALIKES = str.maketrans({
    "[": "［", "]": "］", "(": "（", ")": "）", "*": "∗", "_": "＿", "#": "＃", "`": "ˋ",
    "<": "‹", ">": "›",
})
_HTTP = re.compile(r"http", re.IGNORECASE)
# a dot inside a host-like token (word char before, two letters after): evil.example, www.x
_HOST_DOT = re.compile(r"(?<=\w)\.(?=[^\W\d_]{2})")
_WWW = re.compile(r"www\.", re.IGNORECASE)
_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d]|\.\d)")
# dots NFKC leaves alone but a reader (or an autolinker) may take for "."
_DOT_LOOKALIKES = str.maketrans({"\u3002": ".", "\uff61": "."})


def _keep(ch: str) -> bool:
    if ch == "\n":
        return True
    # Cc = control characters (\r, \x1b ...); Cf = invisible format characters (zero-width
    # spaces, bidi overrides) that could hide "http" from the defang step or reorder text.
    return unicodedata.category(ch) not in ("Cc", "Cf")


def teams_escape(text: str) -> str:
    """Make third-party text inert in an Adaptive Card TextBlock. Pure and deterministic:
    NFKC-normalise, strip control / format characters (keep \\n), swap markdown characters for
    lookalikes, defang URLs (hxxp, [:]//, www[.], [.] in host-like tokens and IPv4 addresses) and
    cap the length at BLOCK_MAX."""
    # NFKC first: fullwidth / compatibility forms (ｈｔｔｐｓ：／／, U+2024) become ASCII and are
    # then defanged like any other text.
    out = unicodedata.normalize("NFKC", text).translate(_DOT_LOOKALIKES)
    out = "".join(ch for ch in out if _keep(ch)).translate(_LOOKALIKES)
    out = _HTTP.sub("hxxp", out).replace("://", "[:]//")
    out = _IPV4.sub(lambda m: m.group(0).replace(".", "[.]"), out)
    out = _WWW.sub(lambda m: m.group(0)[:3] + "[.]", out)
    out = _HOST_DOT.sub("[.]", out)
    if len(out) > BLOCK_MAX:
        out = out[:BLOCK_MAX - 1] + "…"
    return out


def _block(text: str, **extra: Any) -> Dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **extra}


def build_card(header: str, untrusted: List[str]) -> Dict[str, Any]:
    """Teams Workflows message with one Adaptive Card. `header` is our own text; every entry of
    `untrusted` (lead name / company, alert text lines) is escaped into its own TextBlock."""
    lines = [ln for ln in untrusted if ln.strip()]
    body = [_block(header, weight="Bolder")]
    body += [_block(teams_escape(ln)) for ln in lines[:BODY_BLOCKS_MAX]]
    if len(lines) > BODY_BLOCKS_MAX:
        body.append(_block(f"({len(lines) - BODY_BLOCKS_MAX} more lines in the alert log)", isSubtle=True))
    return {"type": "message", "attachments": [{
        "contentType": CARD_CONTENT_TYPE, "contentUrl": None,
        "content": {"$schema": CARD_SCHEMA, "type": "AdaptiveCard", "version": "1.4", "body": body},
    }]}


@dataclass
class _Pending:
    alerter: "TeamsAlerter"
    alert_id: int
    kind: str
    lead_id: Optional[int]
    card: Dict[str, Any]
    now: datetime
    txn: Optional[SessionTransaction]  # innermost savepoint at send time (None = outer)


def _within(txn: Optional[SessionTransaction], ended: SessionTransaction) -> bool:
    while txn is not None:
        if txn is ended:
            return True
        txn = txn.parent
    return False


def _on_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    pending: List[_Pending] = session.info.get(_PENDING, [])
    if not pending:
        return
    if not previous.nested:
        pending.clear()  # outer rollback: every alert row is gone
    else:  # savepoint rollback: only rows written inside that savepoint are gone
        pending[:] = [p for p in pending if not _within(p.txn, previous)]


def _on_after_commit(session: Session) -> None:
    # SQLAlchemy 2.0 also dispatches after_commit when a SAVEPOINT is released; the nested
    # transaction is still current then. Only the outermost commit makes rows durable.
    if session.in_nested_transaction():
        return
    pending: List[_Pending] = session.info.pop(_PENDING, [])
    if not pending:
        return
    bind = session.get_bind()
    for p in pending:
        p.alerter._post(bind, p)


def _hook(session: Session) -> None:
    if not session.info.get(_HOOKED):
        event.listen(session, "after_commit", _on_after_commit)
        event.listen(session, "after_soft_rollback", _on_soft_rollback)
        session.info[_HOOKED] = True


class TeamsAlerter:
    def __init__(self, webhook_url: str, *, client: Optional[httpx.Client] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._url = webhook_url
        self._client = client or make_client()
        self._sleep = sleep

    def send(self, kind: str, text: str, lead: Any, now: datetime,
             session: Optional[Session] = None) -> None:
        """Write the Alert row in the caller's transaction (session, else the lead's session)
        and queue the Teams post for after that transaction commits. Never raises for Teams."""
        lead_id = getattr(lead, "id", None)
        sess = session if session is not None else (object_session(lead) if lead is not None else None)
        if sess is None:
            logger.warning("alert %s for lead %s not stored: no session", kind, lead_id)
            return
        row = AlertRow(lead_id=lead_id, kind=kind, text=text, created_at=now)
        sess.add(row)
        sess.flush()
        # The whole alert text is treated as third-party: callers mix our wording with lead
        # messages, transcripts, model drafts and sender addresses. Lead name / company are fine
        # in Teams (internal sales tool) but never in logs.
        untrusted = text.split("\n")
        if lead is not None:
            header = f"Alert: {kind}, lead {lead_id}"
            untrusted.insert(0, f"{lead.name} @ {lead.company}")
        else:
            header = f"Alert: {kind}, no lead"
        _hook(sess)
        sess.info.setdefault(_PENDING, []).append(
            _Pending(self, row.id, kind, lead_id, build_card(header, untrusted), now,
                     sess.get_nested_transaction()))

    def _deliver(self, card: Dict[str, Any], kind: str, lead_id: Optional[int]) -> bool:
        """POST one card. Never raises; logs the exception type / HTTP status only."""
        try:
            resp = request_with_retry(self._client, "POST", self._url, json=card, idempotent=False,
                                      sleep=self._sleep)
        except Exception as exc:  # noqa: BLE001 - alerting must never break the caller
            # type only: httpx exception messages can contain the webhook URL and its sig
            logger.warning("teams alert %s for lead %s failed: %s", kind, lead_id, type(exc).__name__)
            return False
        if not 200 <= resp.status_code < 300:  # Workflows answers 202 Accepted
            logger.warning("teams alert %s for lead %s: HTTP %s", kind, lead_id, resp.status_code)
            return False
        return True

    def repost(self, session: Session, row: AlertRow, now: datetime) -> bool:
        """Worker retry: post a stored, not-yet-posted Alert row now (synchronously). True when
        Teams accepted it; the caller records posted_at."""
        from .models import Lead

        lead = session.get(Lead, row.lead_id) if row.lead_id is not None else None
        untrusted = (row.text or "").split("\n")
        if lead is not None:
            header = f"Alert: {row.kind}, lead {row.lead_id}"
            untrusted.insert(0, f"{lead.name} @ {lead.company}")
        else:
            header = f"Alert: {row.kind}, no lead"
        return self._deliver(build_card(header, untrusted), row.kind, row.lead_id)

    def post_text(self, header: str, lines: List[str], now: datetime) -> bool:
        """Post a card with no Alert row (the worker's hourly integration-error digest)."""
        return self._deliver(build_card(header, list(lines)), "digest", None)

    def _post(self, bind: Any, p: _Pending) -> None:
        # The card was escaped once when queued; Alert rows keep the raw text, so a later
        # re-post escapes exactly once too. Not idempotent: never retry a maybe-posted card.
        if not self._deliver(p.card, p.kind, p.lead_id):
            return
        try:
            with bind.begin() as conn:
                conn.execute(update(AlertRow).where(AlertRow.id == p.alert_id).values(posted_at=p.now))
        except Exception as exc:  # noqa: BLE001 - worst case the worker re-posts it
            logger.warning("alert %s posted_at not recorded: %s", p.alert_id, type(exc).__name__)


def unposted_alert_ids(session: Session) -> List[int]:
    """Alert rows not yet on Teams (for the worker's retry / digest)."""
    return list(session.scalars(select(AlertRow.id).where(AlertRow.posted_at.is_(None))))
