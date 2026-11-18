from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

# "trap": the honeypot link hidden in our HTML emails. No person can see it, so whatever visits
# it is a security scanner (and its other clicks on that email are scanner clicks too).
TRAP_TAG = "trap"
# voice / chat = the two other product pages, trial = the free trial (AWS Marketplace), task 21
VALID_TAGS = frozenset({"booking", "pricing", "case_study", "product", "voice", "chat", "trial", "homepage",
                        "unsubscribe", TRAP_TAG})
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


# ------------------------------------------------------------------ lead token (Cal.com metadata)
# Same secret as click tokens, but the signed payload starts with "L." so a lead token can never
# decode as a click token (4 dot-separated fields) and a click token can never decode as a lead
# token (no "L." prefix).
LEAD_PREFIX = "L."
LEAD_TOKEN_MAX = 128


def lead_token(lead_id: int, *, settings: Optional[Settings] = None) -> str:
    """Signed lead id for booking links (Cal.com echoes it back as metadata.lead)."""
    if lead_id < 0:
        raise ValueError("lead_id must be non-negative")
    s = settings or get_settings()
    payload = base64.urlsafe_b64encode(f"{LEAD_PREFIX}{lead_id}".encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload, s.link_secret)}"


def parse_lead_token(token: object, *, settings: Optional[Settings] = None) -> Optional[int]:
    """Verify and decode a lead token. Returns None on any problem; never raises."""
    try:
        if not isinstance(token, str) or not token or len(token) > LEAD_TOKEN_MAX:
            return None
        s = settings or get_settings()
        payload, sep, sig = token.partition(".")
        if not sep or not payload or "." in sig:
            return None
        if not hmac.compare_digest(sig.encode(), _sign(payload, s.link_secret).encode()):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        if not raw.startswith(LEAD_PREFIX):
            return None
        lead_s = raw[len(LEAD_PREFIX):]
        if not (lead_s.isascii() and lead_s.isdigit() and len(lead_s) <= 18):
            return None
        return int(lead_s)
    except (ValueError, binascii.Error, UnicodeError, AttributeError, TypeError):
        return None


# ------------------------------------------------------------------ open pixel (our own open tracking)
# Same secret again, payload prefixed "O." so an open token never decodes as a click token
# (4 fields) or a lead token ("L." prefix), and neither of those decodes as an open token.
# Used by the mailpit / gmail tools, which send the emails themselves (Instantly tracks its own opens).
OPEN_PREFIX = "O."
OPEN_TOKEN_MAX = 128
OPEN_SUFFIX = ".gif"


def make_open_pixel(lead_id: int, step: int, *, settings: Optional[Settings] = None) -> str:
    """URL of the 1x1 tracking image for this lead + email step."""
    if lead_id < 0 or step < 0:
        raise ValueError("lead_id and step must be non-negative")
    s = settings or get_settings()
    payload = base64.urlsafe_b64encode(f"{OPEN_PREFIX}{lead_id}.{step}".encode()).decode().rstrip("=")
    return f"{s.link_base_url.rstrip('/')}/o/{payload}.{_sign(payload, s.link_secret)}{OPEN_SUFFIX}"


def parse_open_token(token: object, *, settings: Optional[Settings] = None) -> Optional[Tuple[int, int]]:
    """Verify and decode an open token (with or without the .gif suffix). Returns (lead_id, step)
    or None on any problem; never raises."""
    try:
        if not isinstance(token, str) or not token or len(token) > OPEN_TOKEN_MAX:
            return None
        if token.endswith(OPEN_SUFFIX):
            token = token[: -len(OPEN_SUFFIX)]
        s = settings or get_settings()
        payload, sep, sig = token.partition(".")
        if not sep or not payload or "." in sig:
            return None
        if not hmac.compare_digest(sig.encode(), _sign(payload, s.link_secret).encode()):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        if not raw.startswith(OPEN_PREFIX):
            return None
        lead_s, dot, step_s = raw[len(OPEN_PREFIX):].partition(".")
        if not dot or not all(x.isascii() and x.isdigit() and len(x) <= 18 for x in (lead_s, step_s)):
            return None
        return int(lead_s), int(step_s)
    except (ValueError, binascii.Error, UnicodeError, AttributeError, TypeError):
        return None


def make_trap_url(lead_id: int, step: int, *, settings: Optional[Settings] = None) -> str:
    return make_link(lead_id, step, TRAP_TAG, settings=settings)


def make_unsubscribe_url(lead_id: int, step: int, *, settings: Optional[Settings] = None) -> str:
    """Direct confirm-page URL (/u/<token>) for the List-Unsubscribe header. GET shows the confirm
    page (scanner-safe); POST, which Gmail / Yahoo send for one-click unsubscribe (RFC 8058),
    unsubscribes. Same token as the in-body unsubscribe link."""
    s = settings or get_settings()
    token = make_link(lead_id, step, "unsubscribe", "email", settings=s).rsplit("/r/", 1)[1]
    return f"{s.link_base_url.rstrip('/')}/u/{token}"


# ------------------------------------------------------------------ visit token (real-visit tracking)
# Same secret again, payload prefixed "V." (5 dot-separated fields), so a visit token never decodes
# as a click token (4 fields), a lead token ("L.") or an open token ("O."), and none of those
# decodes as a visit token. It rides on the website URL (?oz_v=...) after a tracked click; the
# site's visits.js snippet sends it back with the reading stats (outreach.visits).
VISIT_PREFIX = "V."
VISIT_TAGS = frozenset({"pricing", "case_study", "product", "voice", "chat", "homepage"})  # our own site's pages
VISIT_PARAM = "oz_v"
VISIT_TOKEN_MAX = 160
VISIT_TOKEN_TTL = timedelta(days=2)  # the beacon comes minutes after the click; a leaked token soon dies


def visit_token(lead_id: int, step: int, tag: str, channel: str = "email", *,
                settings: Optional[Settings] = None, issued_at: Optional[datetime] = None) -> str:
    """Minted when the lead clicks; valid for VISIT_TOKEN_TTL after `issued_at` (default: now)."""
    if tag not in VISIT_TAGS:
        raise ValueError(f"no visit tracking for link tag: {tag!r}")
    if channel not in VALID_CHANNELS:
        raise ValueError(f"unknown link channel: {channel!r}")
    if lead_id < 0 or step < 0:
        raise ValueError("lead_id and step must be non-negative")
    s = settings or get_settings()
    issued = int((issued_at or datetime.now(timezone.utc)).timestamp())
    raw = f"{VISIT_PREFIX}{lead_id}.{step}.{tag}.{channel}.{issued}"
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload, s.link_secret)}"


def parse_visit_token(token: object, *, settings: Optional[Settings] = None,
                      now: Optional[datetime] = None) -> Optional[Tuple[int, int, str, str]]:
    """Verify and decode a visit token: (lead_id, step, tag, channel), or None if invalid or older
    than VISIT_TOKEN_TTL at `now` (default: real time); never raises."""
    try:
        if not isinstance(token, str) or not token or len(token) > VISIT_TOKEN_MAX:
            return None
        s = settings or get_settings()
        payload, sep, sig = token.partition(".")
        if not sep or not payload or "." in sig:
            return None
        if not hmac.compare_digest(sig.encode(), _sign(payload, s.link_secret).encode()):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        if not raw.startswith(VISIT_PREFIX):
            return None
        parts = raw[len(VISIT_PREFIX):].split(".")
        if len(parts) != 5:
            return None
        lead_s, step_s, tag, channel, issued_s = parts
        if not all(x.isascii() and x.isdigit() and len(x) <= 18 for x in (lead_s, step_s, issued_s)):
            return None
        age = (now or datetime.now(timezone.utc)).timestamp() - int(issued_s)
        if age > VISIT_TOKEN_TTL.total_seconds():
            return None
        if tag not in VISIT_TAGS or channel not in VALID_CHANNELS:
            return None
        return int(lead_s), int(step_s), tag, channel
    except (ValueError, binascii.Error, UnicodeError, AttributeError, TypeError):
        return None


def with_visit_token(url: str, token: str) -> str:
    """`url` with ?oz_v=<token> added (any existing oz_v replaced, other parameters kept)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != VISIT_PARAM]
    query.append((VISIT_PARAM, token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
