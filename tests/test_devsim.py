"""Simulator request builders (Task 13): every request is accepted by the real parser / signature
check with the server's secrets, and rejected with a wrong secret."""
from __future__ import annotations

import json

import pytest

from outreach import devsim
from outreach.app import verify_teams_signature
from outreach.booking import parse_cal_webhook
from outreach.emailtool.base import BadSignature
from outreach.emailtool.instantly import parse_instantly_webhook
from outreach.links import parse_token
from outreach.whatsapp.webhook import parse_wa_webhook
from tests.conftest import NOW
from tests.test_local_settings import local

S = local()
LEAD = devsim.SimLead(id=7, email="ana@example.com", phone="+12015550123", step_sent=2, active_channel="email")


def email(req):
    return parse_instantly_webhook(req.headers, req.body, S.tool_webhook_secret, NOW)


@pytest.mark.parametrize("build,kind", [
    (lambda: devsim.email_sent(S, LEAD, NOW, step=3), "sent"),
    (lambda: devsim.email_reply(S, LEAD, NOW, text="yes please"), "reply"),
    (lambda: devsim.email_bounce(S, LEAD, NOW, hard=True), "bounce_hard"),
    (lambda: devsim.email_bounce(S, LEAD, NOW, hard=False), "bounce_soft"),
    (lambda: devsim.email_unsub(S, LEAD, NOW), "unsubscribe"),
    (lambda: devsim.email_open(S, LEAD, NOW), "open"),
])
def test_email_events_parse(build, kind):
    req = build()
    assert req.method == "POST" and req.path == "/webhooks/email"
    [ev] = email(req)
    assert ev.kind == kind and ev.email == "ana@example.com"
    with pytest.raises(BadSignature):
        parse_instantly_webhook(req.headers, req.body, "wrong", NOW)


def test_email_sent_details():
    [ev] = email(devsim.email_sent(S, LEAD, NOW, step=3, minutes_ago=10))
    assert ev.step == 3 and (NOW - ev.occurred_at).total_seconds() == 600
    assert ev.data["campaign_id"] == "local-campaign"
    [r] = email(devsim.email_reply(S, LEAD, NOW, text="Not now, try in March"))
    assert r.data["text"] == "Not now, try in March"


def test_email_needs_an_address():
    with pytest.raises(ValueError):
        devsim.email_reply(S, devsim.SimLead(id=1, phone="+12015550123"), NOW, text="x")


def test_click_link_is_signed():
    req = devsim.click(S, LEAD, tag="pricing")
    assert req.method == "GET" and req.path.startswith("/r/")
    assert parse_token(req.path[3:], settings=S) == (7, 2, "pricing", "email")
    assert "Barracuda" in devsim.click(S, LEAD, tag="homepage", scanner=True).headers["User-Agent"]


@pytest.mark.parametrize("build,kind,data", [
    (lambda: devsim.wa_button(S, LEAD, NOW, button="book_demo"), "wa_button", {"payload": "BOOK_DEMO"}),
    (lambda: devsim.wa_button(S, LEAD, NOW, button="ask_question"), "wa_button", {"payload": "ASK_QUESTION"}),
    (lambda: devsim.wa_text(S, LEAD, NOW, text="STOP"), "wa_text", {"text": "STOP"}),
    (lambda: devsim.wa_status(S, LEAD, NOW, status="delivered", wamid="wamid.X"), "wa_delivered", {}),
    (lambda: devsim.wa_status(S, LEAD, NOW, status="failed", code=131026), "wa_failed", {"code": 131026}),
])
def test_wa_events_parse(build, kind, data):
    req = build()
    assert req.path == "/webhooks/whatsapp"
    [ev] = parse_wa_webhook(req.headers, req.body, S.wa_app_secret)
    assert ev.kind == kind and ev.phone == "+12015550123" and ev.occurred_at == NOW
    assert data.items() <= ev.data.items()
    with pytest.raises(BadSignature):
        parse_wa_webhook(req.headers, req.body, "wrong")


def test_wa_needs_a_phone_and_known_button():
    with pytest.raises(ValueError):
        devsim.wa_text(S, devsim.SimLead(id=1, email="a@example.com"), NOW, text="x")
    with pytest.raises(ValueError):
        devsim.wa_button(S, LEAD, NOW, button="nope")


@pytest.mark.parametrize("scenario,kind", [
    ("cal-booked", "booking_created"), ("cal-cancelled", "booking_cancelled"),
    ("cal-rescheduled", "booking_rescheduled"), ("cal-no-show", "no_show"),
    ("cal-meeting-ended", "meeting_ended"), ("cal-form", "form_submitted"),
])
def test_cal_events_parse(scenario, kind):
    req = devsim.cal_event(S, LEAD, NOW, scenario, size="51-200", timeline="now")
    assert req.path == "/webhooks/cal"
    [ev] = parse_cal_webhook(req.headers, req.body, S.cal_webhook_secret, received_at=NOW, settings=S)
    assert ev.kind == kind and ev.lead_id == 7
    if kind == "form_submitted":
        assert ev.data["fit"] == "high"
    with pytest.raises(BadSignature):
        parse_cal_webhook(req.headers, req.body, "wrong", received_at=NOW, settings=S)


def test_teams_command_signed_with_allowed_identity():
    req = devsim.teams_command(S, NOW, text="done 7")
    assert req.path == "/teams/commands"
    assert verify_teams_signature(req.headers["Authorization"], req.body, S.teams_outgoing_key)
    act = json.loads(req.body)
    assert act["from"]["aadObjectId"] == "local-salesperson"
    assert act["channelData"]["tenant"]["id"] == "local-tenant"
    assert act["text"].endswith("done 7")
