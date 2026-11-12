from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
from typing import Optional, Tuple

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

VALID_TAGS = frozenset({"booking", "pricing", "case_study", "product", "homepage", "unsubscribe"})
VALID_CHANNELS = frozenset({"email", "whatsapp"})
SIG_LEN = 16


def _sign(payload: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return mac[:SIG_LEN]


def make_link(
    lead_id: int, step: int, tag: str, channel: str = "email", *, settings: Optional[Settings] = None
) -> str:
    if tag not in VALID_TAGS:
        raise ValueError(f"unknown link tag: {tag!r}")
    if channel not in VALID_CHANNELS:
        raise ValueError(f"unknown link channel: {channel!r}")
    if lead_id < 0 or step < 0:
        raise ValueError("lead_id and step must be non-negative")
    s = settings or get_settings()
    payload = base64.urlsafe_b64encode(f"{lead_id}.{step}.{tag}.{channel}".encode()).decode().rstrip("=")
    token = f"{payload}.{_sign(payload, s.link_secret)}"
    return f"{s.link_base_url.rstrip('/')}/r/{token}"


def parse_token(token: str, *, settings: Optional[Settings] = None) -> Optional[Tuple[int, int, str, str]]:
    """Verify and decode a token. Returns None on any problem; never raises."""
    try:
        s = settings or get_settings()
        payload, sep, sig = token.partition(".")
        if not sep or not payload or "." in sig:
            return None
        if not hmac.compare_digest(sig.encode(), _sign(payload, s.link_secret).encode()):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        parts = raw.split(".")
        if len(parts) != 4:
            return None
        lead_s, step_s, tag, channel = parts
        if not (lead_s.isascii() and lead_s.isdigit() and step_s.isascii() and step_s.isdigit()):
            return None
        if tag not in VALID_TAGS or channel not in VALID_CHANNELS:
            return None
        return int(lead_s), int(step_s), tag, channel
    except (ValueError, binascii.Error, UnicodeError, AttributeError, TypeError):
        return None
