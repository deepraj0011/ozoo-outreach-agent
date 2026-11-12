"""SlackAlerter: every alert is an Alert row first, then a best-effort Slack post."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
from sqlalchemy.orm import Session, object_session

from .http import make_client, request_with_retry
from .models import Alert as AlertRow

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SlackAlerter:
    def __init__(self, webhook_url: str, *, client: Optional[httpx.Client] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = _utcnow) -> None:
        self._url = webhook_url
        self._client = client or make_client()
        self._sleep = sleep
        self._clock = clock

    def send(self, kind: str, text: str, lead: Any = None, session: Optional[Session] = None) -> None:
        """Write the Alert row (in the caller's transaction), then post to Slack.

        The row write uses `session`, else the lead's own session. Slack failures are logged
        (kind / lead id only) and never raised.
        """
        lead_id = getattr(lead, "id", None)
        sess = session if session is not None else (object_session(lead) if lead is not None else None)
        if sess is not None:
            sess.add(AlertRow(lead_id=lead_id, kind=kind, text=text, created_at=self._clock()))
            sess.flush()
        else:
            logger.warning("alert %s for lead %s not stored: no session", kind, lead_id)

        if lead is not None:
            # Lead name / company are fine in Slack (internal sales tool) but never in logs.
            msg = f"[{kind}] lead #{lead_id} {lead.name} @ {lead.company}: {text}"
        else:
            msg = f"[{kind}] no lead: {text}"
        try:
            resp = request_with_retry(self._client, "POST", self._url, json={"text": msg}, sleep=self._sleep)
            if resp.status_code >= 300:
                logger.warning("slack alert %s for lead %s: HTTP %s", kind, lead_id, resp.status_code)
        except Exception as exc:  # noqa: BLE001 - alerting must never break the caller
            logger.warning("slack alert %s for lead %s failed: %s", kind, lead_id, type(exc).__name__)
