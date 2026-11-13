"""Human takeover (start_takeover) and the Microsoft Teams sales commands (wa / done / help)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outreach.actions import StartTakeover, WaSendText
from outreach.app import create_app
from outreach.classifier import ReplyClass, actions_for_class
from outreach.db import SessionLocal
from outreach.handoff import start_takeover
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead, Suppression
from outreach.states import LeadState as S
from outreach.whatsapp.fake import FakeWhatsApp
from tests.conftest import NOW, make_lead
from tests.fakes import (TEAMS_ALLOWED, TEAMS_CHANNEL_ID, TEAMS_SECRET_BYTES, TEAMS_TENANT_ID,
                         TEAMS_WEBHOOK_URL, FakeWa, make_app_engine, make_deps, make_settings)
from tests.test_webhooks_wa import PHONE, SCANNERS, message_body, sign

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
    assert f"wa {lead.id} your message" in t and f"done {lead.id}" in t  # raw in the DB


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


# ---------------------------------------------------------------- Teams outgoing-webhook commands

_ids = itertools.count(1)
USAGE = ("Commands: wa LEAD_ID MESSAGE (send the lead a WhatsApp message and pause the bot), "
         "done LEAD_ID (give the chat back to the bot), help.")


def teams_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"  # Teams sends 7 fractional digits


def activity(text: str, *, aad: str = "aad-raj", name: str = "Raj", tenant: str = TEAMS_TENANT_ID,
             channel: Optional[str] = TEAMS_CHANNEL_ID, ts: Optional[datetime] = None,
             act_id: Optional[str] = None, html: bool = True) -> Dict[str, Any]:
    channel_data: Dict[str, Any] = {"tenant": {"id": tenant}, "teamsTeamId": "19:team@thread.tacv2"}
    if channel is not None:
        channel_data["channel"] = {"id": channel}
    return {
        "type": "message",
        "id": act_id or f"17000000000{next(_ids)}",
        "timestamp": teams_ts(ts or NOW),
        "serviceUrl": "https://smba.trafficmanager.net/emea/",
        "channelId": "msteams",
        "from": {"id": "29:1abc", "name": name, "aadObjectId": aad},
        "conversation": {"isGroup": True, "id": f"{TEAMS_CHANNEL_ID};messageid=1700000000001",
                         "conversationType": "channel", "tenantId": tenant},
        "recipient": None,
        "textFormat": "plain",
        "text": f"<at>Ozoo Outreach</at> {text}" if html else text,
        "channelData": channel_data,
    }


def teams_headers(body: bytes, key: bytes = TEAMS_SECRET_BYTES) -> Dict[str, str]:
    sig = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()
    return {"Authorization": f"HMAC {sig}", "Content-Type": "application/json"}


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


def post_activity(client, act: Dict[str, Any], **hdr: Any):
    body = json.dumps(act).encode()
    return client.post("/teams/commands", content=body, headers=teams_headers(body, **hdr))


def cmd(client, text: str, **kw: Any):
    return post_activity(client, activity(text, **kw))


def reply(resp) -> str:
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message" and set(data) == {"type", "text"}
    return data["text"]


def reload(s, lead_id) -> Lead:
    s.expire_all()
    return s.get(Lead, lead_id)


def test_create_app_wires_takeover(env):
    _client, _s, deps, _wa = env
    assert deps.takeover is not None


def test_old_chat_route_gone(env):
    client, _s, _deps, _wa = env
    assert client.post("/" + "sl" + "ack/commands", content=b"x").status_code in (404, 405)


# ---- signature

def test_teams_signature_valid_wrong_missing_malformed(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    body = json.dumps(activity(f"wa {lead.id} hi")).encode()
    good = teams_headers(body)
    sig = good["Authorization"][len("HMAC "):]
    bad_headers = [
        teams_headers(body, key=b"another-secret-of-32-bytes-long!"),  # wrong key
        {"Content-Type": "application/json"},  # missing
        {"Authorization": "HMAC %%%not-base64%%%"},  # malformed base64
        {"Authorization": "HMAC "},  # empty
        {"Authorization": sig},  # no "HMAC " prefix
        {"Authorization": f"Bearer {sig}"},
        {"Authorization": f"hmac{sig}"},
    ]
    for h in bad_headers:
        assert client.post("/teams/commands", content=body, headers=h).status_code == 401
    tampered = body.replace(b" hi", b" ho")
    assert client.post("/teams/commands", content=tampered, headers=good).status_code == 401
    assert wa.calls == []
    assert reply(client.post("/teams/commands", content=body, headers=good)) == f"Sending to lead {lead.id}…"
    assert len(wa.calls) == 1


def test_teams_signature_checked_before_json(env):
    client, _s, _deps, _wa = env
    assert client.post("/teams/commands", content=b"{not json",
                       headers={"Authorization": "HMAC AAAA"}).status_code == 401
    assert client.post("/teams/commands", content=b"{not json",
                       headers=teams_headers(b"{not json")).status_code == 400


def test_teams_body_too_large(env):
    client, _s, _deps, _wa = env
    body = json.dumps(activity("wa 1 " + "x" * (64 * 1024))).encode()
    assert client.post("/teams/commands", content=body, headers=teams_headers(body)).status_code == 413


@pytest.mark.parametrize("mutate", [
    lambda a: a.pop("id"),
    lambda a: a.update(id=""),
    lambda a: a.update(id="x" * 300),
    lambda a: a.update(id=5),
    lambda a: a.pop("timestamp"),
    lambda a: a.update(timestamp="yesterday"),
    lambda a: a.update(timestamp=12345),
    lambda a: a.update(timestamp="2026-11-12T10:00:00"),  # no zone
    lambda a: a.pop("text"),
    lambda a: a.update(text=None),
    lambda a: a.pop("from"),
    lambda a: a.update({"from": "raj"}),
    lambda a: a["from"].pop("aadObjectId"),
    lambda a: a.pop("channelData"),
    lambda a: a["channelData"].pop("tenant"),
    lambda a: a["channelData"].update(tenant=[1]),
    lambda a: (a["channelData"].pop("channel"), a.pop("conversation")),
])
def test_teams_missing_fields_400(env, mutate):
    client, s, _deps, wa = env
    lead = add_lead(s)
    act = activity(f"wa {lead.id} hi")
    mutate(act)
    assert post_activity(client, act).status_code == 400
    assert wa.calls == []


@pytest.mark.parametrize("raw", [b"[]", b"null", b"\"text\"", b"1"])
def test_teams_json_not_object_400(env, raw):
    client, _s, _deps, _wa = env
    assert client.post("/teams/commands", content=raw, headers=teams_headers(raw)).status_code == 400


# ---- freshness

def test_teams_stale_and_future_timestamp_401(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert cmd(client, f"wa {lead.id} hi", ts=NOW - timedelta(seconds=301)).status_code == 401
    assert cmd(client, f"wa {lead.id} hi", ts=NOW + timedelta(seconds=301)).status_code == 401
    assert wa.calls == []
    assert reply(cmd(client, f"wa {lead.id} hi", ts=NOW - timedelta(seconds=299))) == f"Sending to lead {lead.id}…"


def test_teams_timestamp_with_offset_accepted(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    act = activity(f"wa {lead.id} hi")
    act["timestamp"] = "2026-11-12T15:30:00+05:30"
    assert reply(post_activity(client, act)) == f"Sending to lead {lead.id}…"


# ---- allow-lists

@pytest.mark.parametrize("kw", [
    {"tenant": "tenant-evil"},
    {"channel": "19:other-channel@thread.tacv2"},
    {"aad": "aad-intruder"},
])
def test_teams_allow_lists(env, kw, caplog):
    caplog.set_level(logging.DEBUG)
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert reply(cmd(client, f"wa {lead.id} hi", **kw)) == "Not allowed here."
    assert reply(cmd(client, f"done {lead.id}", **kw)) == "Not allowed here."
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None
    for v in kw.values():
        assert v not in caplog.text


def test_teams_channel_from_conversation_id(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert reply(cmd(client, f"wa {lead.id} hi", channel=None)) == f"Sending to lead {lead.id}…"
    act = activity(f"wa {lead.id} hi", channel=None)
    act["conversation"]["id"] = "19:other@thread.tacv2;messageid=1"
    assert reply(post_activity(client, act)) == "Not allowed here."
    assert len(wa.calls) == 1


def test_teams_every_allowed_user(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    for aad in TEAMS_ALLOWED:
        assert reply(cmd(client, f"wa {lead.id} hi", aad=aad)) == f"Sending to lead {lead.id}…"
    assert len(wa.calls) == len(TEAMS_ALLOWED)


# ---- replay

def test_teams_replay_has_no_second_effect(env):
    client, s, _deps, wa = env
    lid = add_lead(s).id
    act = activity(f"wa {lid} hi", act_id="dup-1")
    assert reply(post_activity(client, act)) == f"Sending to lead {lid}…"
    assert reply(post_activity(client, act)) == "Already handled."
    assert len(wa.calls) == 1
    assert len(s.scalars(select(ChatMessage)).all()) == 1
    s.rollback()
    done = activity(f"done {lid}", act_id="dup-2")
    assert reply(post_activity(client, done)) == f"Lead {lid} is back with the bot."
    assert reply(cmd(client, f"wa {lid} again")) == f"Sending to lead {lid}…"
    assert reply(post_activity(client, done)) == "Already handled."
    assert reload(s, lid).wa_human_owner == "aad-raj"  # the replayed done changed nothing


# ---- wa

def test_teams_wa_sends_in_background_and_mutes_bot(env):
    client, s, deps, wa = env
    lead = add_lead(s)
    act = activity(f"wa {lead.id} Hi, this is Raj from Ozoo.")
    act["text"] = (f"<p><at>Ozoo Outreach</at> wa {lead.id} Hi, this is Raj from Ozoo.</p>"
                   "<p>Happy to help &amp; answer.</p>")
    assert reply(post_activity(client, act)) == f"Sending to lead {lead.id}…"
    assert wa.calls == [("send_text", PHONE, "Hi, this is Raj from Ozoo.\nHappy to help & answer.")]
    lead = reload(s, lead.id)
    assert lead.wa_human_owner == "aad-raj" and lead.wa_human_owner_name == "Raj"
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


def test_teams_wa_replies_before_sending(env, monkeypatch):
    """The Meta send is a background task: the reply does not wait for it (Teams needs < 5 s)."""
    from starlette.background import BackgroundTasks

    queued = []
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda self, fn, *a, **k: queued.append((fn, a, k)))
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert reply(cmd(client, f"wa {lead.id} hello")) == f"Sending to lead {lead.id}…"
    assert wa.calls == [] and len(queued) == 1
    assert reload(s, lead.id).wa_human_owner == "aad-raj"  # muted before the send


def test_teams_wa_mention_only_text(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    act = activity(f"wa {lead.id} hello", html=False)
    act["text"] = f'<div><at id="0">Ozoo</at>&nbsp;wa&nbsp;{lead.id}&nbsp;hello</div>'
    assert reply(post_activity(client, act)) == f"Sending to lead {lead.id}…"
    assert wa.calls == [("send_text", PHONE, "hello")]


def test_teams_wa_outside_window_returns_error_text(env):
    client, s, _deps, wa = env
    lead = add_lead(s, wa_window_until=NOW - timedelta(minutes=1))
    assert reply(cmd(client, f"wa {lead.id} hello")) == "24h window closed — the lead must message first"
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None


def test_teams_wa_no_consent(env):
    client, s, _deps, wa = env
    lead = add_lead(s, consent_whatsapp=False)
    assert reply(cmd(client, f"wa {lead.id} hello")) == "lead has opted out / no WhatsApp consent"
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None


def test_teams_wa_suppressed(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    s.add(Suppression(value=PHONE, kind="phone", reason="opt_out", created_at=NOW))
    s.commit()
    assert reply(cmd(client, f"wa {lead.id} hello")) == "lead has opted out / no WhatsApp consent"
    assert wa.calls == []
    assert reload(s, lead.id).wa_human_owner is None


def test_teams_wa_background_failure_alerts(env):
    client, s, deps, wa = env
    lead = add_lead(s)
    wa.client.fail_code = 131047
    assert reply(cmd(client, f"wa {lead.id} SECRET-TEXT")) == f"Sending to lead {lead.id}…"
    assert s.scalars(select(ChatMessage)).all() == []
    alert = s.scalars(select(AlertRow)).one()
    assert alert.kind == "integration_error" and alert.lead_id == lead.id
    assert "131047" in alert.text
    assert "SECRET-TEXT" not in alert.text and PHONE not in alert.text
    assert ("send", "integration_error", lead.id) in deps.alerter.calls


@pytest.mark.parametrize("text, expected", [
    ("wa 999999 hello", "Lead 999999 not found."),
    ("wa", USAGE),
    ("wa abc hello", USAGE),
    ("wa 1", USAGE),
    ("WA 999999 hello", "Lead 999999 not found."),
    ("help", USAGE),
    ("", USAGE),
    ("/wa 1 hi", USAGE),
    ("send 1 hi", USAGE),
])
def test_teams_wa_bad_input(env, text, expected):
    client, _s, _deps, wa = env
    assert reply(cmd(client, text)) == expected
    assert wa.calls == []


def test_teams_wa_message_too_long(env):
    client, s, _deps, wa = env
    lead = add_lead(s)
    assert reply(cmd(client, f"wa {lead.id} " + "x" * 4097)) == "Message must be 1-4096 characters."
    assert wa.calls == []
    assert reply(cmd(client, f"wa {lead.id} " + "x" * 4096)) == f"Sending to lead {lead.id}…"


# ---- done

def test_teams_done_releases(env, tmp_path):
    client, s, _deps, _wa = env
    lead = add_lead(s, wa_human_owner="aad-raj", wa_human_owner_name="Raj",
                    variant_flags={"takeover_at": NOW.isoformat()}, wa_bot_turns=6)
    assert reply(cmd(client, f"done {lead.id}")) == f"Lead {lead.id} is back with the bot."
    lead = reload(s, lead.id)
    assert lead.wa_human_owner is None and lead.wa_human_owner_name is None
    assert lead.wa_bot_turns == 0  # the bot gets a fresh turn budget
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


def test_teams_done_unknown_lead(env):
    client, _s, _deps, _wa = env
    assert reply(cmd(client, "done 424242")) == "Lead 424242 not found."
    assert reply(cmd(client, "done x")) == USAGE
    assert reply(cmd(client, "done")) == USAGE


# ---- privacy

def test_teams_command_logs_and_reply_have_no_text_or_phone(env, caplog):
    caplog.set_level(logging.DEBUG)
    client, s, _deps, _wa = env
    lead = add_lead(s)
    text = reply(cmd(client, f"wa {lead.id} SECRET-TEXT"))
    for out_ in (caplog.text, text):
        assert "SECRET-TEXT" not in out_
        assert PHONE not in out_ and PHONE.lstrip("+") not in out_


def test_teams_handler_error_gives_readable_reply(env, monkeypatch):
    import outreach.app as app_mod

    client, s, _deps, wa = env
    lid = add_lead(s).id

    def boom(*_a):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_mod, "check_free_text", boom)
    assert reply(cmd(client, f"wa {lid} hi", act_id="err-1")) == "Internal error — nothing was changed; try again."
    monkeypatch.undo()
    assert wa.calls == []
    assert reload(s, lid).wa_human_owner is None
    s.rollback()
    # nothing was stored (the replay row rolled back too), so the same activity can be retried
    assert reply(cmd(client, f"wa {lid} hi", act_id="err-1")) == f"Sending to lead {lid}…"


def test_fake_wa_records_raw_text(session):
    lead = wa_lead(session)
    wa = FakeWa()
    wa.send_raw_text(session, lead, "hi", NOW, "bot")
    assert wa.calls == [("send_raw_text", lead.id, "hi", "bot")]


def test_takeover_alert_escapes_lead_text_for_teams(session):
    import httpx
    import respx

    from outreach.alerts import TeamsAlerter
    from tests.test_alerts import card_texts

    evil = "[Open CRM](https://evil.example) <at>all</at>"
    with respx.mock:
        route = respx.post(TEAMS_WEBHOOK_URL).mock(return_value=httpx.Response(202))
        lead = wa_lead(session, name="**Eve**", company="<at>x</at> & Co")
        add_history(session, lead, ("lead", evil))
        deps = make_deps(alerter=TeamsAlerter(TEAMS_WEBHOOK_URL, sleep=lambda _s: None))
        start_takeover(session, lead, "unsure", deps, NOW)
        session.commit()
        texts = card_texts(route.calls[0].request)
    posted = "\n".join(texts)
    assert "](" not in posted and "http" not in posted.lower() and "<at>" not in posted
    assert "∗∗Eve∗∗ @ ‹at›x‹/at› & Co" in texts
    assert f"lead: ［Open CRM］（hxxps[:]//evil[.]example） ‹at›all‹/at›" in texts
    assert f"Reply: mention the outreach bot in the sales channel with: wa {lead.id} your message" in texts
    assert evil in session.scalars(select(AlertRow.text)).one()  # stored raw
