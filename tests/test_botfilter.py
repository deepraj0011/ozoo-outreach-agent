from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from outreach.botfilter import Scanners, classify_event, load_scanners
from tests.conftest import NOW

import ipaddress

SC = Scanners(
    ua_substrings=("barracuda", "Proofpoint"),
    cidrs=(ipaddress.ip_network("203.0.113.0/24"),),
    apple_proxy_cidrs=(ipaddress.ip_network("17.0.0.0/8"),),
)
SENT = NOW - timedelta(hours=1)


def ev(kind="click", channel="email", tag="pricing", at=None, ip=None, ua="Mozilla/5.0 (Mac) Chrome"):
    return SimpleNamespace(kind=kind, channel=channel, tag=tag, occurred_at=at or NOW, ip=ip, user_agent=ua)


def test_click_within_60s_of_send_is_bot():
    assert classify_event(ev(at=SENT + timedelta(seconds=59)), SENT, [], SC) == "bot"
    assert classify_event(ev(at=SENT + timedelta(seconds=61)), SENT, [], SC) == "human"


def test_two_tags_within_10s_all_bot():
    a = ev(tag="pricing", at=NOW)
    b = ev(tag="booking", at=NOW + timedelta(seconds=10))
    assert classify_event(a, SENT, [b], SC) == "bot"
    assert classify_event(b, SENT, [a], SC) == "bot"
    c = ev(tag="booking", at=NOW + timedelta(seconds=11))
    assert classify_event(a, SENT, [c], SC) == "human"


def test_same_tag_twice_is_human():
    a = ev(tag="pricing", at=NOW)
    b = ev(tag="pricing", at=NOW + timedelta(seconds=2))
    assert classify_event(a, SENT, [b], SC) == "human"


def test_scanner_user_agent_is_bot():
    assert classify_event(ev(ua="BARRACUDA-Scanner/1"), SENT, [], SC) == "bot"
    assert classify_event(ev(kind="open", ua="x proofpoint y"), SENT, [], SC) == "bot"


def test_scanner_ip_cidr_is_bot():
    assert classify_event(ev(ip="203.0.113.9"), SENT, [], SC) == "bot"


def test_invalid_ip_ignored():
    assert classify_event(ev(ip="not-an-ip"), SENT, [], SC) == "human"


def test_apple_proxy_open_is_machine():
    assert classify_event(ev(kind="open", ip="17.5.6.7"), SENT, [], SC) == "machine"
    assert classify_event(ev(kind="open", ua="Mozilla/5.0"), SENT, [], SC) == "machine"


def test_apple_proxy_click_not_machine():
    assert classify_event(ev(kind="click", ip="17.5.6.7"), SENT, [], SC) == "human"


def test_open_without_ip_or_ua_is_human_hint():
    assert classify_event(ev(kind="open", ip=None, ua=None), SENT, [], SC) == "human"


def test_whatsapp_link_click_skips_60s_rule():
    e = ev(channel="whatsapp", at=SENT + timedelta(seconds=5), ua="Mozilla/5.0 (iPhone)")
    assert classify_event(e, SENT, [], SC) == "human"
    # no multi-tag rule either
    other = ev(channel="whatsapp", tag="booking", at=e.occurred_at + timedelta(seconds=1))
    assert classify_event(e, SENT, [other], SC) == "human"
    p = ev(channel="whatsapp", ua="WhatsApp/2.23.20 A")
    assert classify_event(p, SENT, [], SC) == "bot"


def test_no_sent_at_skips_60s_rule():
    assert classify_event(ev(at=NOW), None, [], SC) == "human"


def test_load_scanners_seed():
    root = Path(__file__).resolve().parent.parent
    sc = load_scanners(root / "config" / "scanners.yaml")
    assert any("barracuda" in s.lower() for s in sc.ua_substrings)
    assert ipaddress.ip_network("17.0.0.0/8") in sc.apple_proxy_cidrs
    # Microsoft 365 Defender Safe Links blocks (MSFT per ARIN) are scanners
    assert ipaddress.ip_network("74.224.0.0/14") in sc.cidrs and ipaddress.ip_network("4.208.0.0/12") in sc.cidrs
    assert classify_event(ev(ip="74.224.61.23"), SENT, [], sc) == "bot"
    assert classify_event(ev(ip="4.213.146.152"), SENT, [], sc) == "bot"
    assert classify_event(ev(ua="Mimecast Link Scanner"), SENT, [], sc) == "bot"
