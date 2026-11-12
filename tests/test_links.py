from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from outreach.links import make_link, parse_token

S = SimpleNamespace(link_secret="secret", link_base_url="https://go.example.com")


def _token(url: str) -> str:
    return url.rsplit("/r/", 1)[1]


def test_round_trip():
    url = make_link(7, 2, "pricing", "whatsapp", settings=S)
    assert url.startswith("https://go.example.com/r/")
    assert parse_token(_token(url), settings=S) == (7, 2, "pricing", "whatsapp")


def test_default_channel_email():
    assert parse_token(_token(make_link(1, 0, "booking", settings=S)), settings=S) == (1, 0, "booking", "email")


def test_tampered_returns_none():
    tok = _token(make_link(7, 2, "pricing", settings=S))
    body, sig = tok.rsplit(".", 1)
    assert parse_token(body + "." + "0" * 16, settings=S) is None
    forged = base64.urlsafe_b64encode(b"8.2.pricing.email").decode().rstrip("=")
    assert parse_token(forged + "." + sig, settings=S) is None


def test_wrong_secret_returns_none():
    tok = _token(make_link(7, 2, "pricing", settings=S))
    other = SimpleNamespace(link_secret="other", link_base_url="x")
    assert parse_token(tok, settings=other) is None


def test_unknown_tag_returns_none_and_make_raises():
    with pytest.raises(ValueError):
        make_link(1, 0, "evil", settings=S)
    with pytest.raises(ValueError):
        make_link(1, 0, "booking", "sms", settings=S)
    import hashlib, hmac
    payload = base64.urlsafe_b64encode(b"1.0.evil.email").decode().rstrip("=")
    sig = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()[:16]
    assert parse_token(payload + "." + sig, settings=S) is None


@pytest.mark.parametrize("tok", ["", "garbage", "a.b", "%%%.%%%", "....", "é.é"])
def test_garbage_never_raises(tok):
    assert parse_token(tok, settings=S) is None


def test_trailing_slash_in_base_url():
    s = SimpleNamespace(link_secret="secret", link_base_url="https://go.example.com/")
    assert make_link(1, 0, "booking", settings=s).startswith("https://go.example.com/r/")
