"""WhatsApp Cloud API client (HTTP) and WhatsAppSender (policy: window, templates, ChatMessage)."""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from outreach.actions import WaPort
from outreach.links import parse_token
from outreach.models import ChatMessage
from outreach.whatsapp.client import WaSendError, WhatsAppClient, WindowClosed
from outreach.whatsapp.fake import FakeWhatsApp
from outreach.whatsapp.sender import WhatsAppSender, load_wa_templates, validate_wa_templates
from tests.conftest import NOW, make_lead
from tests.fakes import make_settings

SETTINGS = make_settings()
URL = "https://graph.facebook.com/v21.0/123/messages"
OK = {"messaging_product": "whatsapp", "contacts": [{"input": "919800000000", "wa_id": "919800000000"}],
      "messages": [{"id": "wamid.OUT1"}]}
PHONE = "+919800000000"


def _client() -> WhatsAppClient:
    return WhatsAppClient(SETTINGS, client=httpx.Client(timeout=10.0), sleep=lambda _s: None)


def _wa_lead(session, **kw):
    defaults = dict(phone=PHONE, email=None, consent_whatsapp=True, active_channel="whatsapp",
                    name="Asha Rao", company="Acme")
    defaults.update(kw)
    return make_lead(session, **defaults)


# ------------------------------------------------------------------ client (HTTP)


@respx.mock
def test_template_payload_shape():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    wamid = _client().send_template(PHONE, "ozoo_intro", "en", ["Asha", "Acme"])
    assert wamid == "wamid.OUT1"
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer wa-token"
    body = json.loads(req.content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "919800000000"  # digits, no "+"
    assert body["type"] == "template"
    assert body["template"]["name"] == "ozoo_intro"
    assert body["template"]["language"] == {"code": "en"}
    params = body["template"]["components"][0]["parameters"]
    assert body["template"]["components"][0]["type"] == "body"
    assert params == [{"type": "text", "text": "Asha"}, {"type": "text", "text": "Acme"}]


@respx.mock
def test_text_and_cta_payload_shape():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    c = _client()
    assert c.send_text(PHONE, "hello") == "wamid.OUT1"
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "text" and body["text"]["body"] == "hello"
    assert c.send_cta_link(PHONE, "Pick a time", "Book a demo", "https://go.example.com/r/x") == "wamid.OUT1"
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "interactive"
    inter = body["interactive"]
    assert inter["type"] == "cta_url"
    assert inter["body"] == {"text": "Pick a time"}
    assert inter["action"] == {"name": "cta_url", "parameters": {
        "display_text": "Book a demo", "url": "https://go.example.com/r/x"}}


@respx.mock
def test_send_not_retried_on_500():
    route = respx.post(URL).mock(return_value=httpx.Response(500, json={"error": {"code": 1}}))
    with pytest.raises(WaSendError) as ei:
        _client().send_text(PHONE, "hello")
    assert route.call_count == 1
    assert ei.value.status == 500 and ei.value.code == 1


@respx.mock
def test_send_error_has_no_token(caplog):
    caplog.set_level(logging.DEBUG)
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {
        "code": 131030, "message": "Recipient phone number +919800000000 not in allowed list wa-token"}}))
    with pytest.raises(WaSendError) as ei:
        _client().send_text(PHONE, "secret message body")
    assert ei.value.code == 131030 and ei.value.status == 400
    for text in (str(ei.value), repr(ei.value), caplog.text):
        assert "wa-token" not in text
        assert "919800000000" not in text
        assert "secret message body" not in text
        assert "allowed list" not in text


@respx.mock
def test_error_without_json_body():
    respx.post(URL).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(WaSendError) as ei:
        _client().send_text(PHONE, "x")
    assert ei.value.code is None and ei.value.status == 502


@respx.mock
def test_success_without_message_id_is_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"messages": []}))
    with pytest.raises(WaSendError):
        _client().send_text(PHONE, "x")


# ------------------------------------------------------------------ sender (policy)


@respx.mock(assert_all_called=False)
def test_send_text_outside_window_raises_without_http_call(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    sender = WhatsAppSender(_client(), SETTINGS)
    for i, until in enumerate((None, NOW, NOW - timedelta(minutes=1))):
        lead = _wa_lead(session, phone=f"+91980000000{i}", wa_window_until=until)
        with pytest.raises(WindowClosed):
            sender.send_text(session, lead, "ask_away", NOW)
        with pytest.raises(WindowClosed):
            sender.send_raw_text(session, lead, "hi", NOW, "human")
    assert not route.called
    assert session.scalars(select(ChatMessage)).all() == []


@respx.mock
def test_send_text_inside_window_stores_chat_message(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    lead = _wa_lead(session, wa_window_until=NOW + timedelta(hours=1))
    WhatsAppSender(_client(), SETTINGS).send_text(session, lead, "ask_away", NOW)
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "text"
    msg = session.scalars(select(ChatMessage)).one()
    assert (msg.direction, msg.author, msg.wa_message_id, msg.lead_id) == ("out", "bot", "wamid.OUT1", lead.id)
    assert msg.text == body["text"]["body"]
    assert msg.created_at == NOW


@respx.mock
def test_send_raw_text_human_author(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    lead = _wa_lead(session, wa_window_until=NOW + timedelta(hours=1))
    WhatsAppSender(_client(), SETTINGS).send_raw_text(session, lead, "Hi, this is Priya", NOW, "human")
    assert json.loads(route.calls.last.request.content)["text"]["body"] == "Hi, this is Priya"
    msg = session.scalars(select(ChatMessage)).one()
    assert (msg.author, msg.text) == ("human", "Hi, this is Priya")


@respx.mock
def test_booking_link_uses_cta_with_tracked_url(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    lead = _wa_lead(session, wa_window_until=NOW + timedelta(hours=1), step_sent=0)
    WhatsAppSender(_client(), SETTINGS).send_text(session, lead, "booking_link", NOW)
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "interactive" and body["interactive"]["type"] == "cta_url"
    url = body["interactive"]["action"]["parameters"]["url"]
    assert url.startswith("https://go.example.com/r/")
    assert parse_token(url.rsplit("/", 1)[1], settings=SETTINGS) == (lead.id, 0, "booking", "whatsapp")
    assert body["interactive"]["action"]["parameters"]["display_text"]
    assert "http" not in body["interactive"]["body"]["text"]


@respx.mock
def test_template_sets_sent_at(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    lead = _wa_lead(session)  # templates do not need an open window
    sender = WhatsAppSender(_client(), SETTINGS)
    sender.send_template(session, lead, "ozoo_intro", NOW)
    assert lead.wa_template_sent_at == NOW and lead.wa_reminder_sent_at is None
    body = json.loads(route.calls.last.request.content)
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]] == ["Asha", "Acme"]
    later = NOW + timedelta(hours=48)
    sender.send_template(session, lead, "ozoo_reminder", later)
    assert lead.wa_reminder_sent_at == later and lead.wa_template_sent_at == NOW
    body = json.loads(route.calls.last.request.content)
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]] == ["Asha"]
    msgs = session.scalars(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert [(m.direction, m.author, m.text) for m in msgs] == [
        ("out", "bot", "[template:ozoo_intro]"), ("out", "bot", "[template:ozoo_reminder]")]


@respx.mock
def test_failed_send_sets_nothing(session):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"code": 131026}}))
    lead = _wa_lead(session)
    with pytest.raises(WaSendError):
        WhatsAppSender(_client(), SETTINGS).send_template(session, lead, "ozoo_intro", NOW)
    assert lead.wa_template_sent_at is None
    assert session.scalars(select(ChatMessage)).all() == []


@respx.mock
def test_template_variables_sanitised(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    lead = _wa_lead(session, name="Asha\r\nBcc: x", company="Ac\x00me\n" + "x" * 200)
    WhatsAppSender(_client(), SETTINGS).send_template(session, lead, "ozoo_intro", NOW)
    params = [p["text"] for p in json.loads(route.calls.last.request.content)["template"]["components"][0]["parameters"]]
    for p in params:
        assert "\r" not in p and "\n" not in p and "\x00" not in p and len(p) <= 80 and p.strip()


def test_sender_never_commits(session, monkeypatch):
    fake = FakeWhatsApp(SETTINGS)
    lead = _wa_lead(session, wa_window_until=NOW + timedelta(hours=1))
    monkeypatch.setattr(session, "commit", lambda: pytest.fail("port committed"))
    fake.send_text(session, lead, "ask_away", NOW)
    fake.send_template(session, lead, "ozoo_intro", NOW)


def test_fake_satisfies_port_and_records(session):
    fake: WaPort = FakeWhatsApp(SETTINGS)
    lead = _wa_lead(session)
    fake.send_template(session, lead, "ozoo_intro", NOW)
    with pytest.raises(WindowClosed):
        fake.send_text(session, lead, "ask_away", NOW)
    assert [c[0] for c in fake.calls] == ["send_template"]
    assert lead.wa_template_sent_at == NOW


# ------------------------------------------------------------------ templates/whatsapp.yaml


def test_texts_have_no_raw_urls():
    t = load_wa_templates()
    for key in ("booking_link", "ask_away", "handoff_notice", "reschedule", "booking_slots", "not_ready_close"):
        assert key in t["texts"]
        assert "http" not in t["texts"][key].lower()
        assert "www." not in t["texts"][key].lower()
    assert t["templates"]["ozoo_intro"] == {"language": "en", "variables": ["first_name", "company"]}
    assert t["templates"]["ozoo_reminder"] == {"language": "en", "variables": ["first_name"]}


def test_marketing_markers_present():
    from outreach.whatsapp.sender import DEFAULT_WA_TEMPLATES

    raw = DEFAULT_WA_TEMPLATES.read_text()
    assert raw.count("# MARKETING: replace") >= 6


def _base():
    return {
        "templates": {"ozoo_intro": {"language": "en", "variables": ["first_name", "company"]},
                      "ozoo_reminder": {"language": "en", "variables": ["first_name"]}},
        "buttons": {"booking_link": "Book a demo"},
        "texts": {k: "Hi {first_name}" for k in (
            "booking_link", "ask_away", "handoff_notice", "reschedule", "booking_slots", "not_ready_close")},
    }


def test_validation_rejects_bad_config():
    validate_wa_templates(_base())  # baseline ok
    bad = _base()
    del bad["texts"]["reschedule"]
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
    bad = _base()
    bad["texts"]["ask_away"] = "Hi {unknown}"
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
    bad = _base()
    bad["texts"]["ask_away"] = "See https://ozoo.ai"
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
    bad = _base()
    bad["templates"]["ozoo_intro"]["variables"] = ["booking_link"]
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
    bad = _base()
    del bad["templates"]["ozoo_reminder"]
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
    bad = _base()
    bad["buttons"]["booking_link"] = "x" * 21  # Meta display_text limit is 20
    with pytest.raises(ValueError):
        validate_wa_templates(bad)
