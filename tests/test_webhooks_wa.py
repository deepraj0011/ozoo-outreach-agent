"""Meta WhatsApp webhook: signature, parsing, GET verification, POST -> ingest."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.db import SessionLocal
from outreach.emailtool.base import BadSignature
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead
from outreach.states import LeadState as S
from outreach.whatsapp.webhook import parse_wa_webhook, verify_signature
from tests.conftest import NOW, make_lead
from tests.fakes import make_app_engine, make_deps, make_settings

SECRET = "wa-app-secret"
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
FIX = Path(__file__).parent / "fixtures"
PHONE = "+919800000000"
TS_IN = datetime.fromtimestamp(1762941700, tz=timezone.utc)
TS_STATUS = datetime.fromtimestamp(1762941600, tz=timezone.utc)


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def sign(body: bytes, secret: str = SECRET) -> dict:
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def message_body(msg: dict) -> bytes:
    data = json.loads(raw("wa_text.json"))
    data["entry"][0]["changes"][0]["value"]["messages"] = [msg]
    return json.dumps(data).encode()


@pytest.fixture
def env():
    engine = make_app_engine()
    deps = make_deps()
    app = create_app(make_settings(), deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False)
    s = SessionLocal(bind=engine)
    yield client, s, deps
    s.close()
    engine.dispose()


def add_wa_lead(s, **kw):
    defaults = dict(phone=PHONE, email=None, consent_whatsapp=True, active_channel="whatsapp")
    defaults.update(kw)
    lead = make_lead(s, **defaults)
    s.commit()
    return lead


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def post(client, body: bytes, headers=None):
    return client.post("/webhooks/whatsapp", content=body, headers=sign(body) if headers is None else headers)


# ------------------------------------------------------------------ signature


def test_signature_prefix_required():
    body = raw("wa_text.json")
    good = sign(body)["X-Hub-Signature-256"]
    verify_signature({"X-Hub-Signature-256": good}, body, SECRET)
    verify_signature({"x-hub-signature-256": good}, body, SECRET)  # case-insensitive header
    hex_only = good.split("=", 1)[1]
    for bad in ({"X-Hub-Signature-256": hex_only}, {"X-Hub-Signature-256": "sha1=" + hex_only},
                {"X-Hub-Signature-256": "sha256="}, {"X-Hub-Signature-256": "sha256=zz"}, {}):
        with pytest.raises(BadSignature):
            verify_signature(bad, body, SECRET)
    with pytest.raises(BadSignature):
        verify_signature({"X-Hub-Signature-256": good}, body + b" ", SECRET)
    with pytest.raises(BadSignature):
        verify_signature({"X-Hub-Signature-256": good}, body, "")


def test_bad_signature_401(env):
    client, s, _ = env
    body = raw("wa_text.json")
    assert post(client, body, {"X-Hub-Signature-256": "sha256=" + "0" * 64}).status_code == 401
    assert post(client, body, {}).status_code == 401
    assert post(client, body, sign(body, "other-secret")).status_code == 401
    assert count(s, Event) == 0


# ------------------------------------------------------------------ parsing


EXPECTED = {
    "wa_status_sent.json": ("wa_sent", "wa:wamid.OUT1:sent", None),
    "wa_status_delivered.json": ("wa_delivered", "wa:wamid.OUT1:delivered", None),
    "wa_status_read.json": ("wa_read", "wa:wamid.OUT1:read", None),
    "wa_status_failed_131026.json": ("wa_failed", "wa:wamid.OUT1:failed", 131026),
    "wa_button_book.json": ("wa_button", "wa:wamid.IN1", None),
    "wa_button_ask.json": ("wa_button", "wa:wamid.IN1", None),
    "wa_text.json": ("wa_text", "wa:wamid.IN1", None),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_parse_each_fixture(name):
    body = raw(name)
    events = parse_wa_webhook(sign(body), body, SECRET)
    assert len(events) == 1
    ev = events[0]
    kind, pid, code = EXPECTED[name]
    assert (ev.kind, ev.provider_event_id, ev.channel, ev.phone) == (kind, pid, "whatsapp", PHONE)
    assert ev.occurred_at.tzinfo is not None
    if kind.startswith("wa_") and kind in ("wa_sent", "wa_delivered", "wa_read", "wa_failed"):
        assert ev.occurred_at == TS_STATUS
        assert ev.data["wamid"] == "wamid.OUT1"
    else:
        assert ev.occurred_at == TS_IN
        assert ev.data["wamid"] == "wamid.IN1"
    if code is not None:
        assert ev.data["code"] == code
    else:
        assert "code" not in ev.data
    if name == "wa_button_book.json":
        assert ev.data["payload"] == "BOOK_DEMO"
    if name == "wa_button_ask.json":
        assert ev.data["payload"] == "ASK_QUESTION"
    if name == "wa_text.json":
        assert ev.data["text"] == "Tell me more"


def test_parse_interactive_button_reply_and_other_types():
    msgs = [
        {"from": "919800000000", "id": "wamid.I2", "timestamp": "1762941700", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "BOOK_DEMO", "title": "Book"}}},
        {"from": "919800000000", "id": "wamid.I3", "timestamp": "1762941700", "type": "image",
         "image": {"id": "media1"}},
        {"from": "919800000000", "id": "wamid.I4", "timestamp": "1762941700", "type": "text",
         "text": {"body": "x" * 5000}},
    ]
    data = json.loads(raw("wa_text.json"))
    data["entry"][0]["changes"][0]["value"]["messages"] = msgs
    body = json.dumps(data).encode()
    a, b, c = parse_wa_webhook(sign(body), body, SECRET)
    assert (a.kind, a.data["payload"], a.provider_event_id) == ("wa_button", "BOOK_DEMO", "wa:wamid.I2")
    assert (b.kind, b.data["text"], b.data["type"]) == ("wa_text", "", "image")
    assert c.kind == "wa_text" and len(c.data["text"]) == 4000


def test_unknown_status_ignored():
    data = json.loads(raw("wa_status_sent.json"))
    data["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = "deleted"
    body = json.dumps(data).encode()
    assert parse_wa_webhook(sign(body), body, SECRET) == []


def test_malformed_json_value_error():
    for body in (b"{bad", b"[]", b'{"entry": "x"}'):
        with pytest.raises(ValueError):
            parse_wa_webhook(sign(body), body, SECRET)


def test_malformed_json_400(env):
    client, _, _ = env
    assert post(client, b"{bad").status_code == 400


def test_oversized_413(env):
    client, s, _ = env
    big = b"x" * (1024 * 1024 + 1)
    assert post(client, big).status_code == 413
    assert count(s, Event) == 0


# ------------------------------------------------------------------ GET verification


def verify_get(client, mode="subscribe", token="wa-verify", challenge="1158201444"):
    return client.get("/webhooks/whatsapp", params={"hub.mode": mode, "hub.verify_token": token,
                                                    "hub.challenge": challenge})


def test_verify_challenge(env):
    client, _, _ = env
    r = verify_get(client)
    assert r.status_code == 200 and r.text == "1158201444"
    assert r.headers["content-type"].startswith("text/plain")
    assert verify_get(client, token="wrong").status_code == 403
    assert verify_get(client, mode="unsubscribe").status_code == 403
    assert client.get("/webhooks/whatsapp").status_code == 403


def test_challenge_non_digit_400(env):
    client, _, _ = env
    assert verify_get(client, challenge="<script>").status_code == 400
    assert verify_get(client, challenge="").status_code == 400
    # wrong token wins over a bad challenge: never reveal that the challenge was checked
    assert verify_get(client, token="nope", challenge="<script>").status_code == 403


# ------------------------------------------------------------------ POST -> ingest


def test_inbound_opens_24h_window(env):
    client, s, _ = env
    lead = add_wa_lead(s)
    r = post(client, raw("wa_text.json"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    s.expire_all()
    lead = s.get(Lead, lead.id)
    assert lead.wa_window_until == TS_IN + timedelta(hours=24)
    msg = s.scalars(select(ChatMessage)).one()
    assert (msg.direction, msg.author, msg.text, msg.wa_message_id) == ("in", "lead", "Tell me more", "wamid.IN1")
    assert lead.state == S.ENGAGED.value


def test_button_opens_window_and_stores_payload(env):
    client, s, _ = env
    lead = add_wa_lead(s)
    assert post(client, raw("wa_button_book.json")).status_code == 200
    s.expire_all()
    assert s.get(Lead, lead.id).wa_window_until == TS_IN + timedelta(hours=24)
    msg = s.scalars(select(ChatMessage)).one()
    assert (msg.text, msg.wa_message_id) == ("BOOK_DEMO", "wamid.IN1")


def test_other_message_type_opens_window(env):
    client, s, _ = env
    lead = add_wa_lead(s)
    body = message_body({"from": "919800000000", "id": "wamid.IMG", "timestamp": "1762941700",
                         "type": "image", "image": {"id": "m1"}})
    assert post(client, body).status_code == 200
    s.expire_all()
    assert s.get(Lead, lead.id).wa_window_until == TS_IN + timedelta(hours=24)
    ev = s.scalars(select(Event).where(Event.channel == "whatsapp")).one()
    assert ev.kind == "wa_text" and ev.data["type"] == "image"
    assert s.scalars(select(ChatMessage)).one().direction == "in"


def test_duplicate_status_acts_once(env):
    client, s, deps = env
    # usable email -> 131026 switches channel via the (recording, state-preserving) outreach fake,
    # so a second, non-deduped delivery would call switch again
    lead = add_wa_lead(s, wa_template_sent_at=NOW, email="lead@acme.com", consent_email=True,
                       verify_status="valid")
    body = raw("wa_status_failed_131026.json")
    assert post(client, body).status_code == 200
    calls_after_first = list(deps.alerter.calls) + list(deps.outreach.calls)
    alerts_after_first = count(s, AlertRow)
    s.expire_all()
    state_after_first = s.get(Lead, lead.id).state
    s.rollback()  # release the shared test connection before the app writes again
    assert post(client, body).status_code == 200  # Meta retry
    assert count(s, Event, Event.kind == "wa_failed") == 1
    assert list(deps.alerter.calls) + list(deps.outreach.calls) == calls_after_first
    assert count(s, AlertRow) == alerts_after_first
    s.expire_all()
    assert s.get(Lead, lead.id).state == state_after_first
    # the first delivery did act, so "once" is not "never"
    assert deps.outreach.calls == [("switch", lead.id, "wa_undeliverable")]


def test_duplicate_read_scores_once(env):
    client, s, _ = env
    lead = add_wa_lead(s, wa_template_sent_at=NOW)
    body = raw("wa_status_read.json")
    for _ in range(3):
        assert post(client, body).status_code == 200
    assert count(s, Event, Event.kind == "wa_read") == 1
    s.expire_all()
    assert s.get(Lead, lead.id).score == 1


def test_unknown_number_alerts_and_no_reply(env):
    client, s, deps = env
    other = add_wa_lead(s, phone="+919811111111")
    assert post(client, raw("wa_text.json")).status_code == 200
    assert count(s, Lead) == 1  # no lead created
    assert ("send", "unmatched_inbound", None) in deps.alerter.calls
    assert count(s, AlertRow, AlertRow.kind == "unmatched_inbound") == 1
    assert deps.wa.calls == []  # no bot reply / template
    assert count(s, ChatMessage) == 0
    ev = s.scalars(select(Event).where(Event.channel == "whatsapp")).one()
    assert ev.lead_id is None and ev.processed
    s.expire_all()
    assert s.get(Lead, other.id).wa_window_until is None


def test_ingest_failure_500(env, monkeypatch):
    client, s, _ = env
    add_wa_lead(s)

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("outreach.app.ingest", boom)
    r = post(client, raw("wa_text.json"))
    assert r.status_code == 500 and r.json() == {"ok": False}


def test_default_deps_wires_whatsapp_sender():
    from outreach.app import default_deps
    from outreach.whatsapp.sender import WhatsAppSender

    deps = default_deps(make_settings())
    assert isinstance(deps.wa, WhatsAppSender)


# ------------------------------------------------------------------ fix round 1


@respx.mock(assert_all_called=False)
def test_stop_then_raw_text_refused_no_http(env):
    from outreach.whatsapp.client import WhatsAppClient, WhatsAppNotAllowed
    from outreach.whatsapp.sender import WhatsAppSender

    route = respx.post(url__startswith="https://graph.facebook.com/").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.X"}]}))
    client, s, _ = env
    lead = add_wa_lead(s)
    body = message_body({"from": "919800000000", "id": "wamid.STOP", "timestamp": "1762941700",
                         "type": "text", "text": {"body": "  STOP "}})
    assert post(client, body).status_code == 200
    s.expire_all()
    lead = s.get(Lead, lead.id)
    assert lead.state == S.SUPPRESSED.value
    sender = WhatsAppSender(WhatsAppClient(make_settings(), client=httpx.Client(timeout=10.0)), make_settings())
    now = TS_IN + timedelta(minutes=5)  # the STOP itself opened the 24 h window
    assert lead.wa_window_until > now
    with pytest.raises(WhatsAppNotAllowed):
        sender.send_raw_text(s, lead, "Sorry to see you go", now, "human")
    with pytest.raises(WhatsAppNotAllowed):
        sender.send_text(s, lead, "ask_away", now)
    assert route.call_count == 0


def test_batch_with_one_broken_item_still_ingests_stop(env):
    client, s, _ = env
    broken_lead = add_wa_lead(s, phone="+919811111111")
    stop_lead = add_wa_lead(s)
    data = json.loads(raw("wa_text.json"))
    data["entry"][0]["changes"][0]["value"]["messages"] = [
        {"from": "919811111111", "id": "wamid.BROKEN", "timestamp": "1762941700", "type": "button"},
        {"from": "919800000000", "id": "wamid.STOP", "timestamp": "1762941700", "type": "text",
         "text": {"body": "stop"}},
    ]
    data["entry"].append("not-an-entry")
    r = post(client, json.dumps(data).encode())
    assert r.status_code == 200
    s.expire_all()
    assert s.get(Lead, stop_lead.id).state == S.SUPPRESSED.value
    assert s.get(Lead, broken_lead.id).state == S.IN_SEQUENCE.value
    assert count(s, Event, Event.channel == "whatsapp") == 1


def test_broken_items_skipped_by_parser(caplog):
    data = json.loads(raw("wa_status_sent.json"))
    value = data["entry"][0]["changes"][0]["value"]
    value["statuses"].insert(0, {"id": "wamid.BAD", "status": "sent", "timestamp": "not-a-number",
                                 "recipient_id": "919800000000"})
    value["statuses"].append(42)
    body = json.dumps(data).encode()
    events = parse_wa_webhook(sign(body), body, SECRET)
    assert [e.provider_event_id for e in events] == ["wa:wamid.OUT1:sent"]
    assert "wamid.BAD" in caplog.text and "919800000000" not in caplog.text
