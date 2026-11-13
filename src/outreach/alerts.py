"""SlackAlerter: every alert is an Alert row first; the Slack post happens only after the
caller's transaction commits (never inside it). Unposted rows keep posted_at NULL for the
worker to retry."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional

import httpx
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, SessionTransaction, object_session

from .http import make_client, request_with_retry
from .models import Alert as AlertRow

logger = logging.getLogger(__name__)

_PENDING = "slack_pending"
_HOOKED = "slack_hooked"


@dataclass
class _Pending:
    alerter: "SlackAlerter"
    alert_id: int
    kind: str
    lead_id: Optional[int]
    message: str
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


class SlackAlerter:
    def __init__(self, webhook_url: str, *, client: Optional[httpx.Client] = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._url = webhook_url
        self._client = client or make_client()
        self._sleep = sleep

    def send(self, kind: str, text: str, lead: Any, now: datetime,
             session: Optional[Session] = None) -> None:
        """Write the Alert row in the caller's transaction (session, else the lead's session)
        and queue the Slack post for after that transaction commits. Never raises for Slack."""
        lead_id = getattr(lead, "id", None)
        sess = session if session is not None else (object_session(lead) if lead is not None else None)
        if sess is None:
            logger.warning("alert %s for lead %s not stored: no session", kind, lead_id)
            return
        row = AlertRow(lead_id=lead_id, kind=kind, text=text, created_at=now)
        sess.add(row)
        sess.flush()
        if lead is not None:
            # Lead name / company are fine in Slack (internal sales tool) but never in logs.
            msg = f"[{kind}] lead #{lead_id} {lead.name} @ {lead.company}: {text}"
        else:
            msg = f"[{kind}] no lead: {text}"
        _hook(sess)
        sess.info.setdefault(_PENDING, []).append(
            _Pending(self, row.id, kind, lead_id, msg, now, sess.get_nested_transaction()))

    def _post(self, bind: Any, p: _Pending) -> None:
        try:
            resp = request_with_retry(self._client, "POST", self._url, json={"text": p.message},
                                      sleep=self._sleep)
            if resp.status_code >= 300:
                logger.warning("slack alert %s for lead %s: HTTP %s", p.kind, p.lead_id, resp.status_code)
                return
        except Exception as exc:  # noqa: BLE001 - alerting must never break the caller
            logger.warning("slack alert %s for lead %s failed: %s", p.kind, p.lead_id, type(exc).__name__)
            return
        try:
            with bind.begin() as conn:
                conn.execute(update(AlertRow).where(AlertRow.id == p.alert_id).values(posted_at=p.now))
        except Exception as exc:  # noqa: BLE001 - worst case the worker re-posts it
            logger.warning("alert %s posted_at not recorded: %s", p.alert_id, type(exc).__name__)


def unposted_alert_ids(session: Session) -> List[int]:
    """Alert rows not yet on Slack (for the worker's retry / digest)."""
    return list(session.scalars(select(AlertRow.id).where(AlertRow.posted_at.is_(None))))
