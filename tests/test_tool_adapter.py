from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from outreach.emailtool.base import BadSignature, NormalizedEvent
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.instantly import BASE_URL, SECRET_HEADER, InstantlyTool
from tests.fakes import WEBHOOK_SECRET, fixture_body

TS = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
H = {SECRET_HEADER: WEBHOOK_SECRET}


def tool(**kw):
    return InstantlyTool(api_key="k", webhook_secret=WEBHOOK_SECRET, sleep=lambda s: None, **kw)


@pytest.mark.parametrize("name,kind", [
    ("tool_sent.json", "sent"),
    ("tool_open.json", "open"),
    ("tool_reply.json", "reply"),
    ("tool_bounce_hard.json", "bounce_hard"),
    ("tool_bounce_soft.json", "bounce_soft"),
    ("tool_unsub.json", "unsubscribe"),
])
def test_each_fixture_maps(name, kind):
    evs = tool().parse_webhook(H, fixture_body(name))
    assert len(evs) == 1
    ev = evs[0]
    assert isinstance(ev, NormalizedEvent)
    assert ev.kind == kind and ev.channel == "email"
    assert ev.occurred_at == TS
    assert ev.email == "lead@example.com"
    assert ev.step == 1
    assert ev.provider_event_id.startswith("instantly:")
    assert ev.data["email_id"] == "22222222-2222-2222-2222-222222222222"
    assert ev.data["campaign_id"] == "11111111-1111-1111-1111-111111111111"


def test_open_carries_ip_ua_and_reply_carries_text():
    (op,) = tool().parse_webhook(H, fixture_body("tool_open.json"))
    assert op.ip == "203.0.113.7" and op.user_agent == "Mozilla/5.0"
    (rp,) = tool().parse_webhook(H, fixture_body("tool_reply.json"))
    assert rp.data["text"] == "Sounds interesting, can we talk?"
    assert rp.data["subject"] == "Re: Quick question"


def test_tool_lead_id_and_email_lowercased():
    (ev,) = tool().parse_webhook(H, fixture_body("tool_reply.json", lead_id="L-9", lead_email="Lead@Example.COM"))
    assert ev.tool_lead_id == "L-9" and ev.email == "lead@example.com"


def test_provider_event_id_stable_and_distinct():
    a = tool().parse_webhook(H, fixture_body("tool_open.json"))[0].provider_event_id
    b = tool().parse_webhook(H, fixture_body("tool_open.json"))[0].provider_event_id
    c = tool().parse_webhook(H, fixture_body("tool_open.json", timestamp="2026-11-12T11:00:00.000Z"))[0]
    assert a == b and c.provider_event_id != a
    assert "lead@example.com" not in a  # no PII in ids


def test_list_payload_and_ignored_event_types():
    body = json.dumps([json.loads(fixture_body("tool_sent.json")),
                       {**json.loads(fixture_body("tool_sent.json")), "event_type": "email_link_clicked"},
                       {**json.loads(fixture_body("tool_sent.json")), "event_type": "something_new"}]).encode()
    evs = tool().parse_webhook(H, body)
    assert [e.kind for e in evs] == ["sent"]


def test_missing_timestamp_uses_received_at():
    body = fixture_body("tool_sent.json", timestamp=None)
    (ev,) = tool().parse_webhook(H, body, received_at=TS)
    assert ev.occurred_at == TS


@pytest.mark.parametrize("headers", [{}, {SECRET_HEADER: "wrong"}, {SECRET_HEADER: ""}])
def test_bad_signature_raises(headers):
    with pytest.raises(BadSignature):
        tool().parse_webhook(headers, fixture_body("tool_sent.json"))


def test_header_lookup_case_insensitive():
    assert tool().parse_webhook({SECRET_HEADER.lower(): WEBHOOK_SECRET}, fixture_body("tool_sent.json"))


def test_malformed_json_raises_value_error():
    with pytest.raises(ValueError):
        tool().parse_webhook(H, b"{not json")


def test_fake_tool_parses_same_and_records_calls():
    fake = FakeEmailTool(webhook_secret=WEBHOOK_SECRET)
    assert fake.parse_webhook(H, fixture_body("tool_sent.json"))[0].kind == "sent"
    with pytest.raises(BadSignature):
        fake.parse_webhook({}, fixture_body("tool_sent.json"))
    fake.stop("t1")
    fake.pause("t2")
    assert fake.calls == [("stop", "t1"), ("pause", "t2")]


# ---------------------------------------------------------------- HTTP API


class _Lead:
    email = "asha@acme.com"
    name = "Asha Rao"
    company = "Acme"


@respx.mock
def test_add_lead_posts_custom_variables():
    route = respx.post(f"{BASE_URL}/leads").mock(return_value=httpx.Response(200, json={"id": "L-1"}))
    assert tool().add_lead("camp-1", _Lead(), {"subject_1": "Hi"}) == "L-1"
    sent = json.loads(route.calls[0].request.content)
    assert sent["campaign"] == "camp-1" and sent["email"] == "asha@acme.com"
    assert sent["custom_variables"] == {"subject_1": "Hi"}
    assert route.calls[0].request.headers["authorization"] == "Bearer k"


@respx.mock
def test_add_lead_http_error_raises():
    respx.post(f"{BASE_URL}/leads").mock(return_value=httpx.Response(400))
    with pytest.raises(httpx.HTTPStatusError):
        tool().add_lead("camp-1", _Lead(), {})


@respx.mock
def test_update_variables_and_stop():
    patch = respx.patch(f"{BASE_URL}/leads/L-1").mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(f"{BASE_URL}/leads/L-1").mock(return_value=httpx.Response(200, json={}))
    t = tool()
    t.update_variables("L-1", {"body_2": "x"})
    t.stop("L-1")
    assert json.loads(patch.calls[0].request.content) == {"custom_variables": {"body_2": "x"}}
    assert delete.called


def test_pause_resume_unsupported_raise():
    from outreach.emailtool.base import ToolUnsupported

    with pytest.raises(ToolUnsupported):
        tool().pause("L-1")
    with pytest.raises(ToolUnsupported):
        tool().resume("L-1")


@respx.mock
def test_reply_uses_latest_email_and_returns_true():
    respx.get(f"{BASE_URL}/emails").mock(return_value=httpx.Response(200, json={"items": [
        {"id": "E-1", "eaccount": "sender@mail.example.com", "subject": "Quick question"}]}))
    route = respx.post(f"{BASE_URL}/emails/reply").mock(return_value=httpx.Response(200, json={}))
    assert tool().reply("L-1", "Thanks!") is True
    sent = json.loads(route.calls[0].request.content)
    assert sent["reply_to_uuid"] == "E-1" and sent["eaccount"] == "sender@mail.example.com"
    assert sent["body"]["text"] == "Thanks!"


@respx.mock
def test_reply_returns_false_when_unsupported():
    respx.get(f"{BASE_URL}/emails").mock(return_value=httpx.Response(200, json={"items": []}))
    assert tool().reply("L-1", "x") is False
    respx.get(f"{BASE_URL}/emails").mock(return_value=httpx.Response(404))
    assert tool().reply("L-1", "x") is False


@respx.mock
def test_mailbox_stats():
    respx.get(f"{BASE_URL}/campaigns/analytics").mock(return_value=httpx.Response(
        200, json=[{"emails_sent_count": 120, "bounced_count": 4}]))
    assert tool().mailbox_stats("camp-1", TS) == {"sent": 120, "bounced": 4}


@respx.mock
def test_add_lead_and_reply_not_retried_on_500():
    leads = respx.post(f"{BASE_URL}/leads").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        tool().add_lead("camp-1", _Lead(), {})
    assert leads.call_count == 1
    respx.get(f"{BASE_URL}/emails").mock(return_value=httpx.Response(200, json={"items": [{"id": "E-1"}]}))
    reply = respx.post(f"{BASE_URL}/emails/reply").mock(side_effect=httpx.ReadTimeout("slow"))
    assert tool().reply("L-1", "x") is False
    assert reply.call_count == 1
