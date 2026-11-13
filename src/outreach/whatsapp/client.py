"""WhatsApp Cloud API HTTP client: template, text and CTA-link sends. No policy here.

Policy (24 h window, which template, ChatMessage log) lives in whatsapp/sender.py.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx

from ..config import Settings
from ..http import make_client, request_with_retry

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com"
MAX_TEXT = 4096  # Meta limit for text.body
MAX_INTERACTIVE_BODY = 1024  # Meta limit for interactive.body.text
_NON_DIGIT = re.compile(r"\D")


class WindowClosed(Exception):
    """Free-form send refused locally: the lead's 24 h customer-service window is not open."""


class WhatsAppNotAllowed(Exception):
    """Send refused locally: no WhatsApp consent, no phone, or the phone is suppressed."""


class WaSendError(Exception):
    """Meta refused or failed the send. Carries only the HTTP status and Meta error code:
    never the token, phone number or message body."""

    def __init__(self, code: Optional[int], status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(f"WhatsApp send failed (HTTP {status}, code {code})")


def _digits(phone: str) -> str:
    d = _NON_DIGIT.sub("", phone or "")
    if not d:
        raise ValueError("phone has no digits")
    return d


def _error_code(resp: httpx.Response) -> Optional[int]:
    try:
        code = resp.json().get("error", {}).get("code")
        return int(code) if code is not None else None
    except (ValueError, AttributeError, TypeError):
        return None


class WhatsAppClient:
    def __init__(self, settings: Settings, client: Optional[httpx.Client] = None, *,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._url = f"{GRAPH_BASE}/{settings.wa_graph_version}/{settings.wa_phone_number_id}/messages"
        self._token = settings.wa_access_token
        self._client = client or make_client()
        self._sleep = sleep

    # ------------------------------------------------------------ transport
    def _post(self, kind: str, phone: str, payload: Dict[str, Any]) -> str:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": _digits(phone),
                **payload}
        resp = request_with_retry(
            self._client, "POST", self._url, idempotent=False, sleep=self._sleep,
            json=body, headers={"Authorization": f"Bearer {self._token}"},
        )
        if not 200 <= resp.status_code < 300:
            err = WaSendError(_error_code(resp), resp.status_code)
            logger.warning("WhatsApp %s send failed: HTTP %s code %s", kind, err.status, err.code)
            raise err
        try:
            wamid = resp.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            wamid = None
        if not isinstance(wamid, str) or not wamid:
            logger.warning("WhatsApp %s send: HTTP %s without message id", kind, resp.status_code)
            raise WaSendError(None, resp.status_code)
        return wamid

    # ------------------------------------------------------------ sends
    def send_template(self, phone: str, name: str, lang: str = "en",
                      variables: Optional[Sequence[str]] = None) -> str:
        template: Dict[str, Any] = {"name": name, "language": {"code": lang}}
        if variables:
            params: List[Dict[str, str]] = [{"type": "text", "text": v} for v in variables]
            template["components"] = [{"type": "body", "parameters": params}]
        return self._post("template", phone, {"type": "template", "template": template})

    def send_text(self, phone: str, text: str) -> str:
        if not text or len(text) > MAX_TEXT:
            raise ValueError(f"text must be 1..{MAX_TEXT} characters")
        return self._post("text", phone, {"type": "text", "text": {"preview_url": False, "body": text}})

    def send_cta_link(self, phone: str, text: str, button_text: str, url: str) -> str:
        if not text or len(text) > MAX_INTERACTIVE_BODY:
            raise ValueError(f"text must be 1..{MAX_INTERACTIVE_BODY} characters")
        return self._post("cta_url", phone, {"type": "interactive", "interactive": {
            "type": "cta_url",
            "body": {"text": text},
            "action": {"name": "cta_url", "parameters": {"display_text": button_text, "url": url}},
        }})
