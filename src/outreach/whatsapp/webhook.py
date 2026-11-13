"""Meta WhatsApp webhook: X-Hub-Signature-256 verification and payload -> NormalizedEvent."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from ..emailtool.base import BadSignature, NormalizedEvent, header

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="
STATUS_KINDS = {"sent": "wa_sent", "delivered": "wa_delivered", "read": "wa_read", "failed": "wa_failed"}
MAX_TEXT = 4000


def verify_signature(headers: Mapping[str, str], raw: bytes, app_secret: str) -> None:
    got = header(headers, SIGNATURE_HEADER) or ""
    if not app_secret or not got.startswith(_PREFIX) or len(got) <= len(_PREFIX):
        raise BadSignature(SIGNATURE_HEADER)
    want = _PREFIX + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got.encode(), want.encode()):
        raise BadSignature(SIGNATURE_HEADER)


def _ts(raw: Any) -> datetime:
    return datetime.fromtimestamp(int(raw), tz=timezone.utc)


def _status_event(st: Dict[str, Any]) -> Optional[NormalizedEvent]:
    kind = STATUS_KINDS.get(st.get("status"))  # unknown statuses are ignored
    if kind is None:
        return None
    wamid = str(st["id"])
    data: Dict[str, Any] = {"wamid": wamid}
    if kind == "wa_failed":
        errors = st.get("errors") or []
        if errors and isinstance(errors[0], dict) and errors[0].get("code") is not None:
            data["code"] = int(errors[0]["code"])
    return NormalizedEvent(
        provider_event_id=f"wa:{wamid}:{st['status']}", channel="whatsapp", kind=kind,
        occurred_at=_ts(st["timestamp"]), phone="+" + str(st["recipient_id"]), data=data,
    )


def _message_event(msg: Dict[str, Any]) -> NormalizedEvent:
    wamid = str(msg["id"])
    mtype = msg.get("type")
    reply = (msg.get("interactive") or {}).get("button_reply") if mtype == "interactive" else None
    if mtype == "button":
        kind, data = "wa_button", {"payload": str(msg["button"]["payload"]), "wamid": wamid}
    elif mtype == "interactive" and isinstance(reply, dict):
        kind, data = "wa_button", {"payload": str(reply["id"]), "wamid": wamid}
    elif mtype == "text":
        kind, data = "wa_text", {"text": str(msg["text"]["body"])[:MAX_TEXT], "wamid": wamid}
    else:
        kind, data = "wa_text", {"text": "", "type": str(mtype), "wamid": wamid}
    return NormalizedEvent(
        provider_event_id=f"wa:{wamid}", channel="whatsapp", kind=kind,
        occurred_at=_ts(msg["timestamp"]), phone="+" + str(msg["from"]), data=data,
    )


def _item_failed(what: str, item: Any, exc: Exception) -> None:
    wamid = item.get("id") if isinstance(item, dict) else None
    logger.warning("WhatsApp webhook: skipped malformed %s %s: %s",
                   what, wamid if isinstance(wamid, str) else "(no id)", type(exc).__name__)


def _items(container: Any, key: str) -> List[Any]:
    items = container.get(key) if isinstance(container, dict) else None
    return items if isinstance(items, list) else []


def parse_wa_webhook(headers: Mapping[str, str], raw: bytes, app_secret: str) -> List[NormalizedEvent]:
    """Verify, then map every status / message in the payload.

    BadSignature on auth failure; ValueError (-> 400) only for an undecodable body, a
    non-object top level or a non-list `entry`. A single malformed item is logged (wamid +
    exception type only) and skipped, so one odd item never blocks the rest of the batch
    (e.g. another lead's STOP) behind Meta's retries.
    """
    verify_signature(headers, raw, app_secret)
    payload = json.loads(raw)  # JSONDecodeError / UnicodeDecodeError are ValueErrors
    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        raise ValueError("entry is not a list")
    events: List[NormalizedEvent] = []
    for entry in entries:
        for change in _items(entry, "changes"):
            value = change.get("value") if isinstance(change, dict) else None
            for st in _items(value, "statuses"):
                try:
                    ev = _status_event(st)
                except Exception as exc:  # noqa: BLE001 - skip only this item
                    _item_failed("status", st, exc)
                    continue
                if ev is not None:
                    events.append(ev)
            for msg in _items(value, "messages"):
                try:
                    events.append(_message_event(msg))
                except Exception as exc:  # noqa: BLE001 - skip only this item
                    _item_failed("message", msg, exc)
    logger.info("WhatsApp webhook: %d events", len(events))
    return events
