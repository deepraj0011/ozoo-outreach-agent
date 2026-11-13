"""Human takeover (start_takeover) and the Slack slash commands /wa and /wa-done."""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outreach.actions import StartTakeover, WaSendText
from outreach.app import create_app
from outreach.classifier import ReplyClass, actions_for_class
from outreach.db import SessionLocal
from outreach.handoff import start_takeover
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead
from outreach.states import LeadState as S
from outreach.whatsapp.fake import FakeWhatsApp
from tests.conftest import NOW, make_lead
from tests.fakes import FakeWa, make_app_engine, make_deps, make_settings
from tests.test_webhooks_wa import PHONE, SCANNERS, message_body, sign

SIGNING = "slack-signing"
SETTINGS = make_settings()


def wa_lead(session, **kw):
    defaults = dict(state=S.ENGAGED, active_channel="whatsapp", phone=PHONE, email=None,
                    consent_whatsapp=True, wa_window_until=NOW + timedelta(hours=10))
    defaults.update(kw)
    return make_lead(session, **defaults)


def add_history(session, lead, *items):
    for i, (author, text) in enumerate(items):
        session.add(ChatMessage(lead_id=lead.id, direction="in" if author == "lead" else "out", author=author,
                                text=text, created_at=NOW - timedelta(minutes=100) + timedelta(minutes=i)))
    session.flush()


# ---------------------------------------------------------------- start_takeover

def test_takeover_alert_contains_transcript_and_usage(session):
    lead = wa_lead(session)
    items = [("lead" if i % 3 == 0 else "bot" if i % 3 == 1 else "human", f"msg{i:02d}") for i in range(12)]
    items[-1] = ("lead", "L" * 400)
    add_history(session, lead, *items)
    deps = make_deps()
    start_takeover(session, lead, "out_of_scope", deps, NOW)
    row = session.scalars(select(AlertRow)).one()
    assert row.kind == "wa_human_needed"
    t = row.text
    assert f"lead {lead.id}" in t and "out_of_scope" in t
    assert "msg00" not in t and "msg01" not in t  # only the last 10 messages
    assert "bot: msg10" in t and "lead: msg09" in t and "human: msg08" in t
    assert "lead: " + "L" * 300 in t and "L" * 301 not in t
    assert t.index("msg02") < t.index("msg10")  # oldest first
    assert f"/wa {lead.id} <message>" in t and f"/wa-done {lead.id}" in t


def test_takeover_sets_flags_and_sends_notice(session):
    lead = wa_lead(session, variant_flags={"takeover_released": "earlier", "x": 1})
    deps = make_deps()
    start_takeover(session, lead, "wants_human", deps, NOW)
    assert lead.variant_flags == {"x": 1, "takeover_at": NOW.isoformat(), "takeover_reason": "wants_human"}
    assert deps.wa.calls == [("send_text", lead.id, "handoff_notice")]
    assert deps.alerter.calls == [("send", "wa_human_needed", lead.id)]


def test_takeover_window_closed_no_notice(session):
    lead = wa_lead(session, wa_window_until=NOW - timedelta(seconds=1))
    deps = make_deps()
    start_takeover(session, lead, "unsure", deps, NOW)
    assert deps.wa.calls == []
    assert lead.variant_flags["takeover_at"] == NOW.isoformat()


def test_takeover_notice_failure_becomes_integration_error(session):
    lead = wa_lead(session)
    deps = make_deps()
    deps.wa.fail.add("send_text")
    start_takeover(session, lead, "unsure", deps, NOW)
    kinds = sorted(session.scalars(select(AlertRow.kind)).all())
    assert kinds == ["integration_error", "wa_human_needed"]
    assert lead.variant_flags["takeover_at"] == NOW.isoformat()


def test_classifier_whatsapp_unsure_starts_takeover(session):
    lead = wa_lead(session)
    ev = Event(lead_id=lead.id, channel="whatsapp", kind="wa_text", occurred_at=NOW, received_at=NOW,
               verdict="human", processed=False, data={"text": "hm"})
    session.add(ev)
    session.flush()
    rc = ReplyClass(label="unsure", confidence=0.0, return_date=None, later_date=None, referral_email=None,
                    referral_phone=None, referral_name=None, reason="r", draft_answer=None)
    assert actions_for_class(rc, lead, ev, NOW) == [StartTakeover("unsure")]
    lead.wa_window_until = NOW - timedelta(minutes=1)
    assert actions_for_class(rc, lead, ev, NOW) == [StartTakeover("unsure")]
    assert WaSendText("handoff_notice") not in actions_for_class(rc, lead, ev, NOW)


# ---------------------------------------------------------------- Slack slash commands

def slack_headers(body: bytes, ts: Optional[int] = None, secret: str = SIGNING) -> Dict[str, str]:
    ts = int(NOW.timestamp()) if ts is None else ts
    base = f"v0:{ts}:".encode() + body
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": str(ts),
            "Content-Type": "application/x-www-form-urlencoded"}


def form(command: str, text: str, user_id: str = "U123") -> bytes:
    return urlencode({"command": command, "text": text, "user_id": user_id, "user_name": "raj",
                      "team_id": "T1", "channel_id": "C1"}).encode()


@pytest.fixture
def env():
    engine = make_app_engine()
    wa = FakeWhatsApp(SETTINGS)
    deps = make_deps(wa=wa)
    app = create_app(SETTINGS, deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False)
    s = SessionLocal(bind=engine)
    yield client, s, deps, wa
    s.close()
    engine.dispose()


def add_lead(s, **kw):
    lead = wa_lead(s, **kw)
    s.commit()
    return lead


def slash(client, command: str, text: str, user_id: str = "U123", **hdr: Any):
    body = form(command, text, user_id)
    return client.post("/slack/commands", content=body, headers=slack_headers(body, **hdr))


def reply(resp) -> str:
    assert resp.status_code == 200
    data = resp.json()
    assert data["response_type"] == "ephemeral"
    return data["text"]


def reload(s, lead_id) -> Lead:
    s.expire_all()
    return s.get(Lead, lead_id)


def test_create_app_wires_takeover(env):
    _client, _s, deps, _wa = env
    assert deps.takeover is not None


def test_slash_wa_sends_and_mutes_bot(env):
    client, s, deps, wa = env
    lead = add_lead(s)
    assert reply(slash(client, "/wa", f"{lead.id} Hi, this is Raj from Ozoo.\nHappy to help.")) == "sent"
    assert wa.calls == [("send_text", PHONE, "Hi, this is Raj from Ozoo.\nHappy to help.")]
    lead = reload(s, lead.id)
    assert lead.wa_human_owner == "U123"
    row = s.scalars(select(ChatMessage)).one()
    assert (row.author, row.direction) == ("human", "out")
    s.rollback()  # end the test session's read transaction (shared StaticPool connection)
    # the lead answers: the bot stays quiet while a human owns the chat
    calls = []
    deps.bot_reply = lambda *a: calls.append(a) or []
    body = message_body({"from": PHONE.lstrip("+"), "id": "wamid.IN1", "timestamp": str(int(NOW.timestamp())),
                         "type": "text", "text": {"body": "thanks"}})
    assert client.post("/webhooks/whatsapp", content=body, headers=sign(body)).status_code == 200
    assert calls == []
    assert len(wa.calls) == 1


def test_slash_wa_outside_window_returns_error_text(env):
    client, s, _deps, wa = env
    lead = add_lead(s, wa_window_until=NOW - timedelta(minutes=1))
    assert reply(slash(client, "/wa", f"{lead.id} hello")) == "24h window closed — the lead must message first"
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None


def test_slash_wa_opted_out_message(env):
    client, s, _deps, wa = env
    lead = add_lead(s, consent_whatsapp=False)
    assert reply(slash(client, "/wa", f"{lead.id} hello")) == "lead has opted out / no WhatsApp consent"
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None


def test_slash_wa_send_error(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    wa.client.fail_code = 131047
    assert reply(slash(client, "/wa", f"{lead.id} hello")) == "send failed (code 131047)"
    assert reload(s, lead.id).wa_human_owner is None
    assert s.scalars(select(ChatMessage)).all() == []


@pytest.mark.parametrize("text, expected", [
    ("999999 hello", "lead not found"),
    ("", "usage: /wa <lead_id> <message>"),
    ("abc hello", "usage: /wa <lead_id> <message>"),
    ("1", "usage: /wa <lead_id> <message>"),
])
def test_slash_wa_bad_input(env, text, expected):
    client, _s, _deps, wa = env
    assert reply(slash(client, "/wa", text)) == expected
    assert wa.calls == []


def test_slash_wa_message_too_long(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert reply(slash(client, "/wa", f"{lead.id} " + "x" * 4097)) == "message must be 1-4096 characters"
    assert wa.calls == []
    assert reply(slash(client, "/wa", f"{lead.id} " + "x" * 4096)) == "sent"


def test_slash_wa_done_releases(env, tmp_path):
    client, s, _deps, _wa = env
    lead = add_lead(s, wa_human_owner="U123", variant_flags={"takeover_at": NOW.isoformat()})
    assert reply(slash(client, "/wa-done", f"{lead.id}")) == "released"
    lead = reload(s, lead.id)
    assert lead.wa_human_owner is None
    assert lead.variant_flags["takeover_released"] == NOW.isoformat()
    # the bot answers again
    from tests.test_chatbot import FakeClient, out
    from outreach.chatbot import make_bot

    add_history(s, lead, ("lead", "one more question"))
    kb = tmp_path / "kb.md"
    kb.write_text("# KB\nfacts")
    fc = FakeClient(out("answer", "sure"))
    assert make_bot(SETTINGS, client=fc, kb_path=kb)(s, lead, NOW) != []
    assert len(fc.calls) == 1


def test_slash_wa_done_unknown_lead(env):
    client, _s, _deps, _wa = env
    assert reply(slash(client, "/wa-done", "424242")) == "lead not found"
    assert reply(slash(client, "/wa-done", "x")) == "usage: /wa-done <lead_id>"


def test_slack_unknown_command(env):
    client, _s, _deps, _wa = env
    assert reply(slash(client, "/nope", "1 hi")) == "unknown command"


def test_slack_bad_signature_401(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    body = form("/wa", f"{lead.id} hi")
    assert client.post("/slack/commands", content=body,
                       headers=slack_headers(body, secret="wrong")).status_code == 401
    h = slack_headers(body)
    del h["X-Slack-Signature"]
    assert client.post("/slack/commands", content=body, headers=h).status_code == 401
    h = slack_headers(body)
    del h["X-Slack-Request-Timestamp"]
    assert client.post("/slack/commands", content=body, headers=h).status_code == 401
    h = slack_headers(body)
    h["X-Slack-Request-Timestamp"] = "not-a-number"
    assert client.post("/slack/commands", content=body, headers=h).status_code == 401
    tampered = form("/wa", f"{lead.id} other")
    assert client.post("/slack/commands", content=tampered, headers=slack_headers(body)).status_code == 401
    assert wa.calls == []


def test_slack_old_timestamp_401(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    now = int(NOW.timestamp())
    assert slash(client, "/wa", f"{lead.id} hi", ts=now - 301).status_code == 401
    assert slash(client, "/wa", f"{lead.id} hi", ts=now + 301).status_code == 401
    assert wa.calls == []
    assert reply(slash(client, "/wa", f"{lead.id} hi", ts=now - 299)) == "sent"


def test_slack_body_too_large(env):
    client, _s, _deps, _wa = env
    body = form("/wa", "1 " + "x" * (64 * 1024))
    assert client.post("/slack/commands", content=body, headers=slack_headers(body)).status_code == 413


def test_slack_user_not_allowed():
    engine = make_app_engine()
    wa = FakeWhatsApp(SETTINGS)
    settings = make_settings(slack_allowed_user_ids="U1, U2")
    app = create_app(settings, deps=make_deps(wa=wa), engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False)
    s = SessionLocal(bind=engine)
    try:
        lead = add_lead(s)
        assert reply(slash(client, "/wa", f"{lead.id} hi", user_id="U9")) == "not allowed"
        assert reply(slash(client, "/wa-done", f"{lead.id}", user_id="U9")) == "not allowed"
        assert wa.calls == []
        assert reply(slash(client, "/wa", f"{lead.id} hi", user_id="U2")) == "sent"
    finally:
        s.close()
        engine.dispose()


def test_slack_command_logs_no_text_or_phone(env, caplog):
    caplog.set_level(logging.DEBUG)
    client, s, _deps, _wa = env
    lead = add_lead(s)
    reply(slash(client, "/wa", f"{lead.id} SECRET-TEXT"))
    assert "SECRET-TEXT" not in caplog.text
    assert PHONE not in caplog.text and PHONE.lstrip("+") not in caplog.text


def test_fake_wa_records_raw_text(session):
    lead = wa_lead(session)
    wa = FakeWa()
    wa.send_raw_text(session, lead, "hi", NOW, "bot")
    assert wa.calls == [("send_raw_text", lead.id, "hi", "bot")]
