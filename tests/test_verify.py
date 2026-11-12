from __future__ import annotations

import logging

import httpx
import respx

from outreach.verify import EmailVerifier


def test_verifier_does_not_log_email_or_key(caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock:
        respx.get("https://api.zerobounce.net/v2/validate").mock(
            return_value=httpx.Response(200, json={"status": "valid"}))
        assert EmailVerifier("SECRETKEY123").check("pii.person@acme.com") == "valid"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pii.person" not in text and "SECRETKEY123" not in text
