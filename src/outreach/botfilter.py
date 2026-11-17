from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple, Union

import yaml

logger = logging.getLogger(__name__)

BOT_SEND_WINDOW = timedelta(seconds=60)
MULTI_TAG_WINDOW = timedelta(seconds=10)
WHATSAPP_PREVIEW_UA_PREFIX = "WhatsApp/"
APPLE_PROXY_UA = "Mozilla/5.0"
TRAP_TAG = "trap"  # links.TRAP_TAG (the hidden honeypot link)
# A trap hit marks other clicks on the same email as scanner clicks when they come from the same
# network (/24 IPv4, /48 IPv6) within this window: scanners fetch every link from one pool.
TRAP_WINDOW = timedelta(hours=1)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCANNERS = REPO_ROOT / "config" / "scanners.yaml"

IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class Scanners:
    ua_substrings: Tuple[str, ...]
    cidrs: Tuple[IPNetwork, ...]
    apple_proxy_cidrs: Tuple[IPNetwork, ...]


def load_scanners(path: Union[str, Path] = DEFAULT_SCANNERS) -> Scanners:
    """Default: the repo's config/scanners.yaml, resolved from this file (not the working directory)."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Scanners(
        ua_substrings=tuple(str(x) for x in (data.get("ua_substrings") or [])),
        cidrs=tuple(ipaddress.ip_network(c, strict=False) for c in (data.get("cidrs") or [])),
        apple_proxy_cidrs=tuple(
            ipaddress.ip_network(c, strict=False) for c in (data.get("apple_proxy_cidrs") or [])
        ),
    )


def _ip_in(ip: Optional[str], nets: Iterable[IPNetwork]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr.version == n.version and addr in n for n in nets)


def _same_network(a: Optional[str], b: Optional[str]) -> bool:
    try:
        x, y = ipaddress.ip_address((a or "").strip()), ipaddress.ip_address((b or "").strip())
    except ValueError:
        return False
    if x.version != y.version:
        return False
    prefix = 24 if x.version == 4 else 48
    return ipaddress.ip_network(f"{x}/{prefix}", strict=False) == ipaddress.ip_network(f"{y}/{prefix}", strict=False)


def _is_scanner(event: Any, scanners: Scanners) -> bool:
    ua_l = (event.user_agent or "").lower()
    return (any(sub.lower() in ua_l for sub in scanners.ua_substrings if sub)
            or _ip_in(event.ip, scanners.cidrs))


def classify_event(event: Any, sent_at: Optional[datetime], sibling_clicks: Iterable[Any], scanners: Scanners) -> str:
    """Return "human", "bot" or "machine" for a link click / open event.

    sibling_clicks: click events of the same lead+step (excluding this one); used for clicks and opens.
    """
    ua = event.user_agent or ""
    is_click = event.kind == "click"

    # (a) WhatsApp clicks: only the link-preview fetcher is a bot.
    if is_click and event.channel == "whatsapp":
        return "bot" if ua.startswith(WHATSAPP_PREVIEW_UA_PREFIX) else "human"

    # (a2) the hidden honeypot link: no person can see it
    if is_click and event.tag == TRAP_TAG:
        return "bot"

    # (b) known scanners
    if _is_scanner(event, scanners):
        return "bot"

    # (b2) the visitor's network also fetched this email as a scanner (its honeypot link, or any
    # link from a listed scanner): scanners load the image and every link from one pool. Catches
    # Microsoft Defender opens from ranges not (yet) in scanners.yaml; Defender runs JavaScript.
    siblings = list(sibling_clicks)
    if any((o.tag == TRAP_TAG or _is_scanner(o, scanners)) and _same_network(o.ip, event.ip)
           and abs(o.occurred_at - event.occurred_at) <= TRAP_WINDOW for o in siblings):
        return "bot"

    # (c) too soon after send
    if sent_at is not None and event.occurred_at - sent_at < BOT_SEND_WINDOW:
        return "bot"

    # (d) several different link tags clicked at once
    if is_click:
        for other in siblings:
            if other.tag != event.tag and abs(other.occurred_at - event.occurred_at) <= MULTI_TAG_WINDOW:
                return "bot"

    # (d2) our click page (CLICK_JS_CONFIRM) was served but its script never ran: a person's
    # browser runs it within a second; most link scanners fetch without running JavaScript
    data = getattr(event, "data", None) or {}
    if is_click and data.get("js_check") and not data.get("js"):
        return "bot"

    # (e) Apple Mail Privacy Protection
    if event.kind == "open" and (ua == APPLE_PROXY_UA or _ip_in(event.ip, scanners.apple_proxy_cidrs)):
        return "machine"

    return "human"
