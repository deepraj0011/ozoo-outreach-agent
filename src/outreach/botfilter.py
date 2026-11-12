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

IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class Scanners:
    ua_substrings: Tuple[str, ...]
    cidrs: Tuple[IPNetwork, ...]
    apple_proxy_cidrs: Tuple[IPNetwork, ...]


def load_scanners(path: Union[str, Path] = "config/scanners.yaml") -> Scanners:
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


def classify_event(event: Any, sent_at: Optional[datetime], sibling_clicks: Iterable[Any], scanners: Scanners) -> str:
    """Return "human", "bot" or "machine" for a link click / open event.

    sibling_clicks: other click events of the same lead+step (excluding this one).
    """
    ua = event.user_agent or ""
    is_click = event.kind == "click"

    # (a) WhatsApp clicks: only the link-preview fetcher is a bot.
    if is_click and event.channel == "whatsapp":
        return "bot" if ua.startswith(WHATSAPP_PREVIEW_UA_PREFIX) else "human"

    # (b) known scanners
    ua_l = ua.lower()
    if any(sub.lower() in ua_l for sub in scanners.ua_substrings if sub):
        return "bot"
    if _ip_in(event.ip, scanners.cidrs):
        return "bot"

    # (c) too soon after send
    if sent_at is not None and event.occurred_at - sent_at < BOT_SEND_WINDOW:
        return "bot"

    # (d) several different link tags clicked at once
    if is_click:
        for other in sibling_clicks:
            if other.tag != event.tag and abs(other.occurred_at - event.occurred_at) <= MULTI_TAG_WINDOW:
                return "bot"

    # (e) Apple Mail Privacy Protection
    if event.kind == "open" and (ua == APPLE_PROXY_UA or _ip_in(event.ip, scanners.apple_proxy_cidrs)):
        return "machine"

    return "human"
