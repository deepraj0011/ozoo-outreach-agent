"""Instantly API v2 adapter (tool chosen in docs/tool-capabilities.md).

Every endpoint / field the capability doc marks UNVERIFIED carries a `# UNVERIFIED:` comment;
confirm each on a trial account before go-live (see the doc's checklist).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

import httpx

from ..http import make_client, request_with_retry
from .base import NormalizedEvent, ToolUnsupported, check_shared_secret

logger = logging.getLogger(__name__)

BASE_URL = "https://api.instantly.ai/api/v2"
# UNVERIFIED: Instantly documents no webhook signing; we configure this custom header on the
# webhook with TOOL_WEBHOOK_SECRET (capability doc fallback) and compare in constant time.
SECRET_HEADER = "X-Ozoo-Webhook-Secret"

_KINDS = {
    "email_sent": "sent",
    "email_opened": "open",
    "reply_received": "reply",
    "lead_unsubscribed": "unsubscribe",
    "email_bounced": None,  # resolved by bounce_type below
}
# Our own redirect is the source of truth for clicks; the tool's link tracking is OFF.
_IGNORED = frozenset({"email_link_clicked"})


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _int(raw: Any) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _str(raw: Any) -> Optional[str]:
    return str(raw) if raw not in (None, "") else None


def _event_id(p: Mapping[str, Any]) -> str:
    """Instantly sends no event id; derive a stable one (no PII in clear)."""
    parts = "|".join(str(p.get(k, "")) for k in
                     ("event_type", "email_id", "lead_email", "timestamp", "step", "campaign_id"))
    return f"instantly:{p.get('event_type')}:{hashlib.sha256(parts.encode()).hexdigest()[:32]}"


def _normalize(p: Mapping[str, Any], received_at: Optional[datetime]) -> Optional[NormalizedEvent]:
    etype = p.get("event_type")
    if etype in _IGNORED:
        return None
    if etype not in _KINDS:
        logger.info("instantly webhook: ignoring event type %s", str(etype)[:50])
        return None
    kind = _KINDS[etype]
    if etype == "email_bounced":
        # UNVERIFIED: hard vs soft bounce field name/values; absent -> treat as hard.
        kind = "bounce_soft" if str(p.get("bounce_type", "")).lower() == "soft" else "bounce_hard"
    occurred = _parse_ts(p.get("timestamp")) or received_at
    if occurred is None:
        raise ValueError("webhook event without timestamp")
    email = _str(p.get("lead_email"))
    data: Dict[str, Any] = {
        "email_id": p.get("email_id"),
        "campaign_id": p.get("campaign_id"),
        "variant": p.get("variant"),
    }
    if kind == "reply":
        data.update(text=p.get("reply_text") or "", subject=p.get("reply_subject") or "")
    return NormalizedEvent(
        provider_event_id=_event_id(p),
        channel="email",
        kind=kind,  # type: ignore[arg-type]
        occurred_at=occurred,
        tool_lead_id=_str(p.get("lead_id")),  # UNVERIFIED: lead id field in webhook payload
        email=email.lower() if email else None,
        step=_int(p.get("step")),  # UNVERIFIED: step number in sent/open payloads
        ip=_str(p.get("ip")),  # UNVERIFIED: open IP (likely absent)
        user_agent=_str(p.get("user_agent")),  # UNVERIFIED: open UA (likely absent)
        data=data,
    )


def parse_instantly_webhook(headers: Mapping[str, str], body: bytes, secret: str,
                            received_at: Optional[datetime] = None) -> List[NormalizedEvent]:
    """Authenticate (raises BadSignature) then map the payload. Malformed JSON -> ValueError."""
    check_shared_secret(headers, SECRET_HEADER, secret)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("malformed webhook body") from exc
    items = payload if isinstance(payload, list) else [payload]
    out: List[NormalizedEvent] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("malformed webhook item")
        ev = _normalize(item, received_at)
        if ev is not None:
            out.append(ev)
    return out


class InstantlyTool:
    def __init__(self, api_key: str, webhook_secret: str, *, client: Optional[httpx.Client] = None,
                 base_url: str = BASE_URL, sleep: Callable[[float], None] = time.sleep) -> None:
        self._secret = webhook_secret
        self._base = base_url.rstrip("/")
        self._client = client or make_client(headers={"Authorization": f"Bearer {api_key}"})
        if client is not None:
            self._client.headers["Authorization"] = f"Bearer {api_key}"
        self._sleep = sleep

    # -------------------------------------------------------------- HTTP plumbing
    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        return request_with_retry(self._client, method, f"{self._base}{path}", sleep=self._sleep, **kw)

    def _ok(self, method: str, path: str, **kw: Any) -> httpx.Response:
        resp = self._call(method, path, **kw)
        resp.raise_for_status()
        return resp

    # -------------------------------------------------------------- EmailTool
    def add_lead(self, campaign_id: str, lead: Any, variables: Mapping[str, str]) -> str:
        # UNVERIFIED: custom_variables field limits.
        body = {
            "campaign": campaign_id,
            "email": lead.email,
            "first_name": (lead.name or "").split(" ")[0],
            "company_name": lead.company,
            "custom_variables": dict(variables),
        }
        return str(self._ok("POST", "/leads", json=body, idempotent=False).json()["id"])

    def update_variables(self, tool_lead_id: str, variables: Mapping[str, str]) -> None:
        # UNVERIFIED: whether in-flight steps pick up changed custom_variables.
        self._ok("PATCH", f"/leads/{tool_lead_id}", json={"custom_variables": dict(variables)})

    def pause(self, tool_lead_id: str) -> None:
        # UNVERIFIED: no documented per-lead pause (C3). Raise so the executor records an
        # integration_error alert and a human pauses the lead in the Instantly UI.
        raise ToolUnsupported("instantly per-lead pause")

    def resume(self, tool_lead_id: str) -> None:
        # UNVERIFIED: no documented per-lead resume (C3).
        raise ToolUnsupported("instantly per-lead resume")

    def stop(self, tool_lead_id: str) -> None:
        resp = self._call("DELETE", f"/leads/{tool_lead_id}")
        if resp.status_code == 404:  # UNVERIFIED: a deleted lead answers 404; already stopped
            return
        resp.raise_for_status()

    def block(self, email: str) -> None:
        # UNVERIFIED: POST /block-lists-entries with {"bl_value": email} (Instantly v2 block list).
        self._ok("POST", "/block-lists-entries", json={"bl_value": email})

    def find_lead(self, email: str) -> Optional[str]:
        """The tool lead id for `email`, or None when the tool has no such lead."""
        # UNVERIFIED: POST /leads/list with a search filter; items carry "id" and "email".
        resp = self._ok("POST", "/leads/list", json={"search": email, "limit": 10})
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        for item in items or []:
            if isinstance(item, dict) and str(item.get("email", "")).lower() == email.lower() and item.get("id"):
                return str(item["id"])
        return None

    def reply(self, tool_lead_id: str, body: str) -> bool:
        """Reply in thread to the lead's latest email. False if C4 is not usable."""
        try:
            # UNVERIFIED: GET /emails filter by lead and the item fields (id, eaccount, subject).
            resp = self._call("GET", "/emails", params={"lead": tool_lead_id, "limit": 1})
            if resp.status_code != 200:
                return False
            items = resp.json().get("items") or []
            if not items:
                return False
            last = items[0]
            # UNVERIFIED: POST /emails/reply body shape.
            r = self._call("POST", "/emails/reply", json={
                "reply_to_uuid": last["id"],
                "eaccount": last.get("eaccount"),
                "subject": last.get("subject") or "",
                "body": {"text": body},
            }, idempotent=False)
            return 200 <= r.status_code < 300
        except (httpx.HTTPError, ValueError, KeyError, AttributeError) as exc:
            logger.warning("instantly reply failed: %s", type(exc).__name__)
            return False

    def parse_webhook(self, headers: Mapping[str, str], body: bytes,
                      received_at: Optional[datetime] = None) -> List[NormalizedEvent]:
        return parse_instantly_webhook(headers, body, self._secret, received_at)

    def mailbox_stats(self, campaign_id: str, since: datetime) -> Dict[str, int]:
        # UNVERIFIED: analytics endpoint params and count field names.
        resp = self._ok("GET", "/campaigns/analytics",
                        params={"id": campaign_id, "start_date": since.date().isoformat()})
        data = resp.json()
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        return {"sent": int(row.get("emails_sent_count") or 0),
                "bounced": int(row.get("bounced_count") or 0)}
