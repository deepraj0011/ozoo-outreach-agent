from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from outreach.app import create_app
from outreach.botfilter import Scanners, load_scanners
from outreach.db import SessionLocal
from outreach.emailtool.base import NormalizedEvent
from outreach.emailtool.instantly import SECRET_HEADER
from outreach.ingest import ingest
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead, Suppression
from outreach.states import LeadState as S
from tests.conftest import NOW, make_lead
from tests.fakes import WEBHOOK_SECRET, fixture_body, make_app_engine, make_deps, make_settings

H = {SECRET_HEADER: WEBHOOK_SECRET}
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())


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


def add_lead(s, **kw):
    lead = make_lead(s, **kw)
    s.commit()
    return lead


def count(s, model, *where):
    return s.scalar(select(func.count()).select_from(model).where(*where))


def post(client, body, headers=H):
    return client.post("/webhooks/email", content=body, headers=headers)


# ------------------------------------------------------------------ routes


def test_bad_signature_401(env):
    client, s, _ = env
    assert post(client, fixture_body("tool_reply.json"), {SECRET_HEADER: "nope"}).status_code == 401
    assert post(client, fixture_body("tool_reply.json"), {}).status_code == 401
    assert count(s, Event) == 0


def test_webhook_oversized_413(env):
    client, s, _ = env
    big = b"x" * (1024 * 1024 + 1)
    assert post(client, big).status_code == 413
    # lying Content-Length still caught by actual length
    r = client.post("/webhooks/email", content=big, headers={**H, "Content-Length": "10"})
    assert r.status_code in (400, 413)
    assert count(s, Event) == 0


def test_malformed_json_400(env):
    client, _, _ = env
    assert post(client, b"{bad").status_code == 400


def test_sent_webhook_sets_step(env):
    client, s, deps = env
    lead = add_lead(s, email="lead@example.com", active_channel="email")
    r = post(client, fixture_body("tool_sent.json"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    s.expire_all()
    lead = s.get(Lead, lead.id)
    assert lead.step_sent == 1 and lead.last_sent_at == NOW
    ev = s.scalars(select(Event).where(Event.kind == "sent")).one()
    assert ev.lead_id == lead.id and ev.processed and ev.verdict == "n/a"
    assert deps.outreach.names() == ["refresh_next_step"]


def test_duplicate_webhook_acts_once(env):
    client, s, deps = env
    lead = add_lead(s, email="lead@example.com", tool_lead_id="T-1", active_channel="email")
    body = fixture_body("tool_reply.json")
    assert post(client, body).status_code == 200
    assert post(client, body).status_code == 200
    s.expire_all()
    assert count(s, Event, Event.kind == "reply") == 1
    assert deps.tool.calls == [("stop", "T-1")]
    assert s.get(Lead, lead.id).state == "engaged"


def test_reply_matched_by_tool_lead_id(env):
    client, s, _ = env
    by_id = add_lead(s, email="someone@acme.com", tool_lead_id="T-77")
    add_lead(s, email="lead@example.com")  # would match by email
    assert post(client, fixture_body("tool_reply.json", lead_id="T-77")).status_code == 200
    ev = s.scalars(select(Event).where(Event.kind == "reply")).one()
    assert ev.lead_id == by_id.id and ev.verdict == "human"
    assert "matched_by" not in ev.data and "sender_email" not in ev.data
    s.expire_all()
    assert s.get(Lead, by_id.id).state == "engaged"


def test_reply_from_colleague_matches_by_domain(env):
    client, s, deps = env
    older = add_lead(s, email="ceo@bigco.com", domain="bigco.com", created_at=NOW - timedelta(days=2))
    newer = add_lead(s, email="cto@bigco.com", domain="bigco.com", created_at=NOW - timedelta(days=1))
    add_lead(s, email="gone@bigco.com", domain="bigco.com", state=S.SUPPRESSED, created_at=NOW)
    assert post(client, fixture_body("tool_reply.json", lead_email="colleague@BigCo.com")).status_code == 200
    ev = s.scalars(select(Event).where(Event.kind == "reply")).one()
    assert ev.lead_id == newer.id
    assert ev.data["matched_by"] == "domain"
    assert ev.data["sender_email"] == "colleague@bigco.com"
    s.expire_all()
    assert s.get(Lead, newer.id).state == "engaged"
    assert s.get(Lead, older.id).state == "paused"  # PauseColleagues


def test_reply_free_mail_no_domain_match(env):
    client, s, deps = env
    add_lead(s, email="a@gmail.com", domain="gmail.com")
    post(client, fixture_body("tool_reply.json", lead_email="b@gmail.com"))
    ev = s.scalars(select(Event).where(Event.kind == "reply")).one()
    assert ev.lead_id is None
    assert count(s, AlertRow, AlertRow.kind == "unmatched_reply") == 1


def test_reply_unmatched_alerts(env):
    client, s, deps = env
    assert post(client, fixture_body("tool_reply.json", lead_email="nobody@nowhere.io")).status_code == 200
    ev = s.scalars(select(Event)).one()
    assert ev.lead_id is None and ev.processed
    assert ("send", "unmatched_reply", None) in deps.alerter.calls
    assert count(s, AlertRow, AlertRow.kind == "unmatched_reply") == 1
    assert count(s, Lead) == 0


def test_open_unmatched_stored_without_alert(env):
    client, s, deps = env
    post(client, fixture_body("tool_open.json", lead_email="nobody@nowhere.io"))
    assert count(s, Event) == 1 and deps.alerter.calls == []


def test_unsubscribe_webhook_suppresses(env):
    client, s, deps = env
    lead = add_lead(s, email="lead@example.com", phone="+919812345678", consent_whatsapp=True,
                    tool_lead_id="T-1")
    post(client, fixture_body("tool_unsub.json"))
    s.expire_all()
    assert s.get(Lead, lead.id).state == "suppressed"
    assert count(s, Suppression) == 2


def test_hard_bounce_switches_channel(env):
    client, s, deps = env
    lead = add_lead(s, email="lead@example.com", phone="+919812345678", consent_whatsapp=True,
                    verify_status="valid", active_channel="email", tool_lead_id="T-1")
    post(client, fixture_body("tool_bounce_hard.json"))
    s.expire_all()
    lead = s.get(Lead, lead.id)
    assert lead.verify_status == "invalid"
    assert ("switch", lead.id, "bounce") in deps.outreach.calls


def test_ingest_failure_returns_500_without_detail(env):
    client, s, deps = env
    add_lead(s, email="lead@example.com", active_channel="email")

    def boom(*a, **k):
        raise RuntimeError("secret detail lead@example.com")

    import outreach.ingest as ing
    orig = ing.decide
    ing.decide = boom
    try:
        r = post(client, fixture_body("tool_sent.json"))
    finally:
        ing.decide = orig
    assert r.status_code == 500 and r.json() == {"ok": False}
    assert "secret" not in r.text
    assert count(s, Event) == 0  # rolled back; provider retry is safe


# ------------------------------------------------------------------ ingest direct


def nev(**kw):
    d = dict(provider_event_id="p-1", channel="email", kind="open", occurred_at=NOW)
    d.update(kw)
    return NormalizedEvent(**d)


def test_ingest_error_rolls_back_and_raises(session):
    lead = make_lead(session, email="x@acme.com")
    session.commit()

    deps = make_deps()

    import outreach.ingest as ing
    orig = ing.execute

    def bad_execute(*a, **k):
        raise RuntimeError("boom")

    ing.execute = bad_execute
    try:
        with pytest.raises(RuntimeError):
            ingest(session, [nev(kind="sent", step=1, email="x@acme.com")], deps, NOW, SCANNERS)
    finally:
        ing.execute = orig
    assert count(session, Event) == 0
    assert session.get(Lead, lead.id).step_sent == 0


def test_ingest_earlier_events_committed_before_failure(session):
    make_lead(session, email="x@acme.com")
    session.commit()
    import outreach.ingest as ing
    orig = ing.decide
    calls = []

    def flaky(lead, ev, now):
        calls.append(ev.kind)
        if len(calls) == 2:
            raise RuntimeError("x")
        return orig(lead, ev, now)

    ing.decide = flaky
    try:
        with pytest.raises(RuntimeError):
            ingest(session, [nev(provider_event_id="a", kind="sent", step=1, email="x@acme.com"),
                             nev(provider_event_id="b", kind="sent", step=2, email="x@acme.com")],
                   make_deps(), NOW, SCANNERS)
    finally:
        ing.decide = orig
    assert [e.provider_event_id for e in session.scalars(select(Event).where(Event.kind == "sent"))] == ["a"]


def test_ingest_open_verdict_bot_within_60s_of_send(session):
    lead = make_lead(session, email="x@acme.com", step_sent=1, last_sent_at=NOW - timedelta(seconds=30))
    session.commit()
    ingest(session, [nev(email="x@acme.com", step=1, user_agent="Thunderbird", ip="198.51.100.2")],
           make_deps(), NOW, SCANNERS)
    ev = session.scalars(select(Event)).one()
    assert ev.verdict == "bot" and ev.processed
    assert session.get(Lead, lead.id).score == 0


def test_ingest_open_human_scores(session):
    lead = make_lead(session, email="x@acme.com", step_sent=1, last_sent_at=NOW - timedelta(hours=2))
    session.commit()
    ingest(session, [nev(email="X@ACME.com", step=1, user_agent="Thunderbird", ip="198.51.100.2")],
           make_deps(), NOW, SCANNERS)
    ev = session.scalars(select(Event)).one()
    assert ev.verdict == "human" and ev.lead_id == lead.id
    assert session.get(Lead, lead.id).score == 1


def test_ingest_open_apple_mpp_machine(session):
    make_lead(session, email="x@acme.com")
    session.commit()
    ingest(session, [nev(email="x@acme.com", step=1, user_agent="Mozilla/5.0")],
           make_deps(), NOW, load_scanners("config/scanners.yaml"))
    assert session.scalars(select(Event)).one().verdict == "machine"


def test_ingest_click_deferred_to_worker(session):
    lead = make_lead(session, email="x@acme.com")
    session.commit()
    ingest(session, [nev(kind="click", tag="pricing", email="x@acme.com", step=1)], make_deps(), NOW, SCANNERS)
    ev = session.scalars(select(Event)).one()
    assert ev.processed is False and ev.verdict is None and ev.tag == "pricing"
    assert session.get(Lead, lead.id).score == 0


def test_ingest_lead_id_takes_precedence(session):
    a = make_lead(session, email="a@acme.com")
    b = make_lead(session, email="b@acme.com")
    session.commit()
    ingest(session, [nev(kind="sent", step=1, lead_id=b.id, email="a@acme.com")], make_deps(), NOW, SCANNERS)
    assert session.scalars(select(Event)).one().lead_id == b.id


def test_ingest_unknown_lead_id_is_unmatched(session):
    ingest(session, [nev(kind="unsubscribe", lead_id=999)], make_deps(), NOW, SCANNERS)
    assert session.scalars(select(Event)).one().lead_id is None


def test_ingest_wa_text_opens_window_before_decide(session):
    """Ruling 10: rules must see wa_window_open for this very inbound."""
    lead = make_lead(session, email=None, phone="+919812345678", consent_whatsapp=True,
                     active_channel="whatsapp", state=S.QUALIFYING)
    session.commit()
    deps = make_deps(bot_reply=lambda s, l, now: [])
    # BOOK_DEMO in QUALIFYING -> WaSendText("booking_link")
    ingest(session, [nev(provider_event_id="w1", channel="whatsapp", kind="wa_button",
                         phone="+919812345678", data={"payload": "BOOK_DEMO", "text": "Book a demo"})],
           deps, NOW, SCANNERS)
    lead = session.get(Lead, lead.id)
    assert lead.wa_window_until == NOW + timedelta(hours=24)
    assert deps.wa.calls == [("send_text", lead.id, "booking_link")]
    msg = session.scalars(select(ChatMessage)).one()
    assert msg.direction == "in" and msg.author == "lead" and msg.text == "Book a demo"
    ev = session.scalars(select(Event).where(Event.kind == "wa_button")).one()
    assert ev.verdict == "human"


def test_ingest_unmatched_wa_inbound_alerts(session):
    deps = make_deps()
    ingest(session, [nev(channel="whatsapp", kind="wa_text", phone="+919800000000", data={"text": "hi"})],
           deps, NOW, SCANNERS)
    assert deps.alerter.calls == [("send", "unmatched_inbound", None)]
    assert count(session, ChatMessage) == 0


def test_ingest_wa_read_human(session):
    lead = make_lead(session, email=None, phone="+919812345678", consent_whatsapp=True, active_channel="whatsapp")
    session.commit()
    ingest(session, [nev(channel="whatsapp", kind="wa_read", phone="+919812345678")], make_deps(), NOW, SCANNERS)
    assert session.scalars(select(Event)).one().verdict == "human"
    assert session.get(Lead, lead.id).score == 1


def test_ingest_unmatched_alert_failure_isolated(session):
    deps = make_deps()
    deps.alerter.fail.add("send")
    ingest(session, [nev(kind="reply", email="nobody@nowhere.io", data={"text": "hi"})], deps, NOW, SCANNERS)
    ev = session.scalars(select(Event)).one()
    assert ev.processed and ev.lead_id is None
    errs = session.scalars(select(AlertRow).where(AlertRow.kind == "integration_error")).all()
    assert len(errs) == 1 and errs[0].lead_id is None and "nobody" not in errs[0].text
