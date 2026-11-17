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


# ---------------------------------------------------------------- Reacher (open source, self-hosted)
import pytest

from outreach.verify import VERIFY_ERROR, ReacherVerifier, make_verifier

REACHER = "http://reacher.internal:8080/v0/check_email"


@pytest.mark.parametrize("body,expected", [
    ({"is_reachable": "safe"}, "valid"),
    ({"is_reachable": "invalid"}, "invalid"),
    ({"is_reachable": "risky", "smtp": {"is_catch_all": True}}, "catch_all"),
    ({"is_reachable": "risky", "misc": {"is_disposable": True}}, "invalid"),
    ({"is_reachable": "risky", "misc": {"is_role_account": True}}, "unknown"),
    ({"is_reachable": "unknown"}, "unknown"),
    ({}, VERIFY_ERROR),
])
def test_reacher_mapping(body, expected):
    with respx.mock:
        respx.post(REACHER).mock(return_value=httpx.Response(200, json=body))
        assert ReacherVerifier("http://reacher.internal:8080/").check("a@b.com") == expected


def test_reacher_sends_secret_and_never_logs_it(caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock:
        route = respx.post(REACHER).mock(return_value=httpx.Response(500))
        assert ReacherVerifier("http://reacher.internal:8080", "TOPSECRET").check("pii.person@acme.com") == VERIFY_ERROR
        assert route.calls[0].request.headers["x-reacher-secret"] == "TOPSECRET"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "pii.person" not in text and "TOPSECRET" not in text


def test_reacher_unreachable_is_verify_error():
    with respx.mock:
        respx.post(REACHER).mock(side_effect=httpx.ConnectError("down"))
        assert ReacherVerifier("http://reacher.internal:8080").check("a@b.com") == VERIFY_ERROR


def test_provider_choice_and_required_url():
    from tests.fakes import make_settings
    s = make_settings(app_env="local", verify_mode="real", verify_provider="reacher", reacher_url="http://r:8080")
    assert isinstance(make_verifier(s), ReacherVerifier)
    with pytest.raises(Exception, match="reacher_url"):
        make_settings(app_env="local", verify_mode="real", verify_provider="reacher")
