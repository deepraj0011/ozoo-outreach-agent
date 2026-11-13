"""Task 11: Cal.com webhooks (booking, no-show, meeting ended, routing form), fit routing,
lead token on booking links, and the sales handoff summary."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from outreach.actions import (
    Alert, SetFlag, SetNurtureUntil, SetState, StopAllOutreach,
)
from outreach.app import create_app
from outreach.booking import (
    SUMMARY_MAX, build_handoff_summary, fit_from_answers, make_handoff, parse_cal_webhook,
)
from outreach.botfilter import Scanners
from outreach.db import SessionLocal
from outreach.emailtool.base import BadSignature
from outreach.links import lead_token, make_link, parse_lead_token, parse_token
from outreach.models import Alert as AlertRow
from outreach.models import ChatMessage, Event, Lead
from outreach.rules import EventSnapshot, LeadSnapshot, decide
from outreach.states import TERMINAL, LeadState as S
from tests.conftest import NOW, make_lead
from tests.fakes import FakeAlerter, LINK_TARGETS, make_app_engine, make_deps, make_settings

SECRET = "cal-secret"
SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
FIX = Path(__file__).parent / "fixtures"
SETTINGS = make_settings()
ATTENDEE = "lead@example.com"


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def dumps(data) -> bytes:
    return json.dumps(data).encode()


def sign(body: bytes, secret: str = SECRET) -> dict:
    return {"X-Cal-Signature-256": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def booking(trigger: str = "BOOKING_CREATED", *, uid: str = "bk_uid_123", lead: str = "lead_abc123",
            email: str = ATTENDEE, **extra) -> dict:
    data = fixture("cal_booking_created.json")
    data["triggerEvent"] = trigger
    p = data["payload"]
    p["uid"] = uid
    p["metadata"] = {"lead": lead}
    p["attendees"][0]["email"] = email
    p.update(extra)
    return data


def no_show(uid: str = "bk_uid_123", no_show_flag: bool = True, email: str = ATTENDEE) -> dict:
    data = fixture("cal_no_show.json")
    data["payload"]["bookingUid"] = uid
    data["payload"]["attendees"][0]["noShow"] = no_show_flag
    data["payload"]["attendees"][0]["email"] = email
    return data


def form(size, timeline, *, response_id: int = 1, email: str = ATTENDEE, lead: str = "lead_abc123") -> dict:
    data = fixture("cal_form_submitted.json")
    p = data["payload"]
    p["responseId"] = response_id
    p["metadata"] = {"lead": lead}
    p["responses"]["email"] = {"label": "Email", "value": email, "response": email}
    if size is not None:
        p["responses"]["company_size"] = {"label": "Company size", "value": size, "response": size}
    if timeline is not None:
        p["responses"]["timeline"] = {"label": "Timeline", "value": timeline, "response": timeline}
    return data


@pytest.fixture
def env():
    engine = make_app_engine()
    deps = make_deps()
    app = create_app(SETTINGS, deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    s = SessionLocal(bind=engine)
    yield client, s, deps
    s.close()
    engine.dispose()


def post(client, data, headers=None):
    body = data if isinstance(data, bytes) else dumps(data)
    return client.post("/webhooks/cal", content=body, headers=sign(body) if headers is None else headers)


def add_lead(s, **kw):
    defaults = dict(email=ATTENDEE, verify_status="valid", active_channel="email")
    defaults.update(kw)
    lead = make_lead(s, **defaults)
    s.commit()
    return lead


def fresh(s, lead_id: int) -> Lead:
    """A detached copy of the lead; the shared test connection is released before the app writes
    again (SQLite in-memory + StaticPool: one connection for both sessions)."""
    s.expire_all()
    lead = s.get(Lead, lead_id)
    s.expunge(lead)
    s.rollback()
    return lead


def alerts(s, kind=None):
    q = select(AlertRow)
    if kind:
        q = q.where(AlertRow.kind == kind)
    return list(s.scalars(q))


def reasons(s, lead_id):
    return [e.data["reason"] for e in s.scalars(
        select(Event).where(Event.lead_id == lead_id, Event.kind == "state_change").order_by(Event.id))]


# ------------------------------------------------------------------ lead token (ruling 1)

def test_lead_token_roundtrip():
    tok = lead_token(42, settings=SETTINGS)
    assert parse_lead_token(tok, settings=SETTINGS) == 42


def test_lead_token_not_interchangeable_with_click_token():
    tok = lead_token(42, settings=SETTINGS)
    click = make_link(42, 1, "booking", "email", settings=SETTINGS).rsplit("/", 1)[1]
    assert parse_token(tok, settings=SETTINGS) is None
    assert parse_lead_token(click, settings=SETTINGS) is None
    assert parse_lead_token(tok, settings=make_settings(link_secret="X" * 40)) is None
    tampered = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    assert parse_lead_token(tampered, settings=SETTINGS) is None


@pytest.mark.parametrize("bad", ["", "lead_abc123", "a.b", ".", "L.5", None, 5, "x" * 5000,
                                 "TC4tMQ.0000000000000000"])
def test_parse_lead_token_never_raises(bad):
    assert parse_lead_token(bad, settings=SETTINGS) is None


def test_lead_token_rejects_negative():
    with pytest.raises(ValueError):
        lead_token(-1, settings=SETTINGS)


def test_booking_redirect_appends_metadata(env):
    client, s, _ = env
    lead = add_lead(s)
    tok = make_link(lead.id, 1, "booking", settings=SETTINGS).rsplit("/", 1)[1]
    r = client.get(f"/r/{tok}")
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    want = urlsplit(LINK_TARGETS["booking"])
    assert (loc.scheme, loc.netloc, loc.path) == (want.scheme, want.netloc, want.path)
    q = parse_qs(loc.query)
    assert parse_lead_token(q["metadata[lead]"][0], settings=SETTINGS) == lead.id
    # other tags are untouched
    tok = make_link(lead.id, 1, "pricing", settings=SETTINGS).rsplit("/", 1)[1]
    assert client.get(f"/r/{tok}").headers["location"] == LINK_TARGETS["pricing"]


def test_booking_redirect_keeps_existing_query():
    engine = make_app_engine()
    targets = dict(LINK_TARGETS, booking="https://cal.com/ozoo/demo?month=2026-11&metadata[lead]=evil")
    settings = make_settings(link_targets=targets)
    app = create_app(settings, deps=make_deps(), engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, follow_redirects=False)
    tok = make_link(7, 1, "booking", settings=settings).rsplit("/", 1)[1]
    loc = urlsplit(client.get(f"/r/{tok}").headers["location"])
    assert loc.netloc == "cal.com" and loc.path == "/ozoo/demo"
    q = parse_qs(loc.query)
    assert q["month"] == ["2026-11"]
    assert len(q["metadata[lead]"]) == 1  # ours replaces a configured one
    assert parse_lead_token(q["metadata[lead]"][0], settings=settings) == 7
    engine.dispose()


def test_booking_redirect_bad_token_goes_home(env):
    client, _, _ = env
    r = client.get("/r/" + lead_token(1, settings=SETTINGS))  # a lead token is not a link
    assert r.headers["location"] == LINK_TARGETS["homepage"]


# ------------------------------------------------------------------ fit (ruling 2)

@pytest.mark.parametrize("size,timeline,fit", [
    ("51-200", "within 3 months", "high"),
    ("200+", "now", "high"),
    ("50", "immediately", "high"),
    ("1000+", "ASAP", "high"),
    ("51-200", "1-3 months", "high"),
    ("51-200", "4-6 months", "medium"),
    ("51-200", "6+ months", "medium"),
    ("51-200", "exploring", "medium"),
    ("11-50", "exploring", "medium"),
    ("10", None, "medium"),
    ("1-10", "within 6 months", "medium"),
    ("1-10", "3 months", "medium"),
    ("1-10", "exploring", "low"),
    ("1-10", "not sure", "low"),
    ("1-10", "12 months", "low"),
    (None, None, "low"),
    ("lots", "soon-ish", "low"),
    ("", "", "low"),
    ("9", "7 months", "low"),
])
def test_fit_parsing_variants(size, timeline, fit):
    assert fit_from_answers(size, timeline, settings=SETTINGS) == fit


def test_fit_thresholds_from_settings():
    strict = make_settings(fit_high_min_size=500, fit_medium_min_size=100, fit_high_max_months=1,
                           fit_medium_max_months=1)
    assert fit_from_answers("51-200", "now", settings=strict) == "medium"
    assert fit_from_answers("51-200", "3 months", settings=strict) == "low"


def test_fit_settings_validated():
    with pytest.raises(Exception):
        make_settings(fit_high_min_size=-1)
    with pytest.raises(Exception):
        make_settings(fit_high_min_size=5, fit_medium_min_size=10)
    with pytest.raises(Exception):
        make_settings(fit_high_max_months=9, fit_medium_max_months=6)


def test_fit_handles_huge_input():
    assert fit_from_answers("9" * 5000, "1" * 5000, settings=SETTINGS) in ("high", "medium", "low")


# ------------------------------------------------------------------ parser

def test_parse_booking_created():
    body = dumps(booking())
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.kind == "booking_created" and ev.channel == "cal"
    assert ev.provider_event_id == "cal:BOOKING_CREATED:bk_uid_123"
    assert ev.email == ATTENDEE and ev.lead_id is None
    assert ev.data["uid"] == "bk_uid_123"
    assert ev.data["start"] == "2026-11-20T09:00:00Z" and ev.data["end"] == "2026-11-20T09:30:00Z"


def test_parse_lowercases_email_and_reads_token():
    tok = lead_token(9, settings=SETTINGS)
    body = dumps(booking(email="Lead@Example.COM", lead=tok))
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.email == ATTENDEE and ev.lead_id == 9


def test_parse_phone_from_responses():
    body = dumps(booking(responses={"attendeePhoneNumber": {"label": "Phone", "value": "+91 98000 00000"}}))
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.phone == "+919800000000"


def test_parse_bad_phone_ignored():
    body = dumps(booking(responses={"phone": {"value": "not a phone"}}))
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.phone is None


def test_flat_meeting_ended_parsed():
    body = (FIX / "cal_meeting_ended.json").read_bytes()
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.kind == "meeting_ended" and ev.provider_event_id == "cal:MEETING_ENDED:bk_uid_123"
    assert ev.email == ATTENDEE and ev.data["end"] == "2026-11-20T09:30:00Z"


def test_parse_no_show():
    body = dumps(no_show())
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.kind == "no_show" and ev.provider_event_id == "cal:BOOKING_NO_SHOW_UPDATED:bk_uid_123:noshow"
    assert ev.email == ATTENDEE


def test_no_show_false_skipped(env):
    body = dumps(no_show(no_show_flag=False))
    assert parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS) == []
    client, s, _ = env
    lead = add_lead(s, state=S.BOOKED)
    assert post(client, body).status_code == 200
    assert fresh(s, lead.id).state == S.BOOKED.value
    assert s.scalar(select(func.count()).select_from(Event).where(Event.channel == "cal")) == 0


def test_parse_form_submitted():
    body = dumps(form("51-200", "within 3 months", response_id=77))
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.kind == "form_submitted" and ev.data["fit"] == "high"
    assert ev.provider_event_id == "cal:FORM_SUBMITTED:form_1:77"
    assert ev.email == ATTENDEE
    assert ev.data["responses"]["company_size"] == "51-200"


def test_form_responses_capped():
    data = form("x" * 5000, "y" * 5000)
    for i in range(200):
        data["payload"]["responses"][f"f{i}" + "k" * 300] = {"value": "v" * 2000}
    body = dumps(data)
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    resp = ev.data["responses"]
    assert len(resp) <= 30
    assert all(len(k) <= 64 and len(v) <= 500 for k, v in resp.items())
    assert len(json.dumps(ev.data)) < 40_000


def test_form_without_response_id_gets_stable_id():
    data = form("1-10", "exploring")
    del data["payload"]["responseId"]
    body = dumps(data)
    [a] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    [b] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert a.provider_event_id == b.provider_event_id and len(a.provider_event_id) <= 200


def test_unknown_trigger_ignored(env):
    body = dumps(booking("BOOKING_REQUESTED"))
    assert parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS) == []
    client, s, _ = env
    assert post(client, body).status_code == 200
    assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_long_uid_id_capped():
    body = dumps(booking(uid="u" * 1000))
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert len(ev.provider_event_id) <= 200


def test_signature_checked_before_json():
    with pytest.raises(BadSignature):
        parse_cal_webhook({}, b"not json", SECRET, received_at=NOW, settings=SETTINGS)
    with pytest.raises(BadSignature):
        parse_cal_webhook(sign(b"x", "other"), b"x", SECRET, received_at=NOW, settings=SETTINGS)
    with pytest.raises(ValueError):
        parse_cal_webhook(sign(b"not json"), b"not json", SECRET, received_at=NOW, settings=SETTINGS)


@pytest.mark.parametrize("data", [
    [], "x", 1, None, {}, {"triggerEvent": 5}, {"triggerEvent": "BOOKING_CREATED"},
    {"triggerEvent": "BOOKING_CREATED", "payload": "x"},
    {"triggerEvent": "BOOKING_CREATED", "payload": {"uid": None, "attendees": "x"}},
    {"triggerEvent": "BOOKING_CREATED", "payload": {"uid": {"a": 1}}},
    {"triggerEvent": "FORM_SUBMITTED", "payload": {"responses": "x"}},
    {"triggerEvent": "BOOKING_NO_SHOW_UPDATED", "payload": {"attendees": [1, "x", None]}},
    {"triggerEvent": "MEETING_ENDED"},
])
def test_malformed_payloads_never_500(env, data):
    client, _, _ = env
    r = post(client, dumps(data))
    assert r.status_code in (200, 400)


def test_parser_tolerates_odd_shapes():
    data = booking(attendees=[None, {"email": 5}, {"email": ATTENDEE}], responses=[1, 2],
                   metadata="x", startTime=5)
    body = dumps(data)
    [ev] = parse_cal_webhook(sign(body), body, SECRET, received_at=NOW, settings=SETTINGS)
    assert ev.email == ATTENDEE and ev.lead_id is None


# ------------------------------------------------------------------ route

def test_bad_signature_401(env):
    client, s, _ = env
    add_lead(s)
    body = dumps(booking())
    assert post(client, body, headers={"X-Cal-Signature-256": "0" * 64}).status_code == 401
    assert post(client, body, headers={}).status_code == 401
    assert post(client, body, headers=sign(body, "wrong")).status_code == 401
    assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_body_cap_413(env):
    client, _, _ = env
    body = b"{" + b" " * (1024 * 1024 + 10) + b"}"
    assert post(client, body).status_code == 413


def test_malformed_json_400(env):
    client, _, _ = env
    assert post(client, b"{nope").status_code == 400


def test_booking_created_moves_to_booked(env):
    client, s, deps = env
    lead = add_lead(s)
    r = post(client, booking())
    assert r.status_code == 200 and r.json() == {"ok": True}
    lead = fresh(s, lead.id)
    assert lead.state == S.BOOKED.value
    assert lead.variant_flags["booking_uid"] == "bk_uid_123"
    assert lead.variant_flags["booking_end"] == "2026-11-20T09:30:00Z"
    assert [a.kind for a in alerts(s)] == ["booked"]
    ev = s.scalars(select(Event).where(Event.channel == "cal")).one()
    assert ev.lead_id == lead.id and ev.processed


def test_booking_from_whatsapp_lead(env):
    client, s, _ = env
    lead = add_lead(s, email=None, phone="+919800000001", consent_whatsapp=True, active_channel="whatsapp",
                    state=S.QUALIFYING)
    link = make_link(lead.id, 0, "booking", "whatsapp", settings=SETTINGS)
    loc = client.get("/r/" + link.rsplit("/", 1)[1]).headers["location"]
    tok = parse_qs(urlsplit(loc).query)["metadata[lead]"][0]
    assert post(client, booking(lead=tok, email="someone@else.com")).status_code == 200
    assert fresh(s, lead.id).state == S.BOOKED.value


def test_match_by_metadata_token_over_email(env):
    client, s, _ = env
    by_email = add_lead(s)
    by_token = add_lead(s, email="other@acme.com", state=S.ENGAGED)
    tok = lead_token(by_token.id, settings=SETTINGS)
    assert post(client, booking(lead=tok)).status_code == 200
    assert fresh(s, by_token.id).state == S.BOOKED.value
    assert fresh(s, by_email.id).state == S.IN_SEQUENCE.value


def test_forged_token_falls_back_to_email(env):
    client, s, _ = env
    by_email = add_lead(s)
    other = add_lead(s, email="other@acme.com")
    forged = lead_token(other.id, settings=make_settings(link_secret="F" * 40))
    assert post(client, booking(lead=forged)).status_code == 200
    assert fresh(s, by_email.id).state == S.BOOKED.value
    assert fresh(s, other.id).state == S.IN_SEQUENCE.value


def test_match_by_phone(env):
    client, s, _ = env
    lead = add_lead(s, email=None, phone="+919800000002", consent_whatsapp=True, active_channel="whatsapp")
    data = booking(email="nobody@else.com", responses={"attendeePhoneNumber": {"value": "+919800000002"}})
    assert post(client, data).status_code == 200
    assert fresh(s, lead.id).state == S.BOOKED.value


def test_unmatched_booking_alerts(env):
    client, s, _ = env
    assert post(client, booking(email="stranger@nowhere.com")).status_code == 200
    [a] = alerts(s)
    assert a.kind == "unmatched_booking" and a.lead_id is None and "stranger@nowhere.com" in a.text
    ev = s.scalars(select(Event)).one()
    assert ev.lead_id is None and ev.processed


def test_duplicate_booking_webhook_acts_once(env):
    client, s, deps = env
    lead = add_lead(s)
    body = dumps(booking())
    assert post(client, body).status_code == 200
    assert post(client, body).status_code == 200
    assert s.scalar(select(func.count()).select_from(Event).where(Event.channel == "cal")) == 1
    assert [a.kind for a in alerts(s)] == ["booked"]
    assert reasons(s, lead.id) == ["booking_created"]


def test_form_submitted_low_fit_to_nurture(env):
    client, s, deps = env
    lead = add_lead(s, state=S.QUALIFYING, tool_lead_id="t-1")
    assert post(client, form("1-10", "exploring")).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.NURTURE.value
    assert reasons(s, lead.id) == ["low_fit"]
    assert lead.nurture_until == NOW + timedelta(days=90)
    assert lead.variant_flags["fit"] == "low"
    assert ("stop", "t-1") in deps.tool.calls


def test_form_submitted_high_fit_sets_flag(env):
    client, s, _ = env
    lead = add_lead(s, state=S.QUALIFYING)
    assert post(client, form("51-200", "now")).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.QUALIFYING.value and lead.variant_flags["fit"] == "high"


def test_no_show_first_time(env):
    client, s, deps = env
    lead = add_lead(s, state=S.BOOKED)
    assert post(client, no_show()).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.QUALIFYING.value
    assert [a.kind for a in alerts(s)] == ["no_show"]
    assert ("send_email_text", lead.id, "reschedule") in deps.outreach.calls


def test_no_show_first_time_whatsapp(env):
    client, s, deps = env
    lead = add_lead(s, email=None, phone="+919800000003", consent_whatsapp=True, active_channel="whatsapp",
                    state=S.BOOKED, wa_window_until=NOW + timedelta(hours=3))
    data = no_show(email="x@y.com")
    data["payload"]["metadata"] = {"lead": lead_token(lead.id, settings=SETTINGS)}
    assert post(client, data).status_code == 200
    assert fresh(s, lead.id).state == S.QUALIFYING.value
    assert ("send_text", lead.id, "reschedule") in deps.wa.calls


def test_no_show_second_time_to_nurture(env):
    client, s, _ = env
    lead = add_lead(s, state=S.BOOKED)
    assert post(client, no_show("bk_1")).status_code == 200
    assert post(client, booking(uid="bk_2")).status_code == 200
    assert fresh(s, lead.id).state == S.BOOKED.value
    assert post(client, no_show("bk_2")).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.NURTURE.value and lead.variant_flags["no_show_count"] == 2
    assert reasons(s, lead.id) == ["no_show", "booking_created", "no_show_x2"]


def test_cancel_moves_back_to_qualifying(env):
    client, s, _ = env
    lead = add_lead(s)
    assert post(client, booking()).status_code == 200
    assert post(client, booking("BOOKING_CANCELLED")).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.QUALIFYING.value
    assert reasons(s, lead.id)[-1] == "booking_cancelled"
    assert [a.kind for a in alerts(s)] == ["booked", "booked"]


def test_cancel_of_older_booking_ignored(env):
    client, s, _ = env
    lead = add_lead(s)
    assert post(client, booking(uid="bk_new")).status_code == 200
    assert post(client, booking("BOOKING_CANCELLED", uid="bk_old")).status_code == 200
    assert fresh(s, lead.id).state == S.BOOKED.value


def test_reschedule_updates_end(env):
    client, s, _ = env
    lead = add_lead(s)
    assert post(client, booking()).status_code == 200
    data = booking("BOOKING_RESCHEDULED", uid="bk_uid_456", startTime="2026-11-25T09:00:00Z",
                   endTime="2026-11-25T09:30:00Z")
    assert post(client, data).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.BOOKED.value
    assert lead.variant_flags["booking_end"] == "2026-11-25T09:30:00Z"
    assert lead.variant_flags["booking_uid"] == "bk_uid_456"


def test_meeting_ended_hands_off(env):
    client, s, deps = env
    lead = add_lead(s, name="Asha Rao", company="Acme", role="CTO", source="webinar",
                    phone="+919800000009", consent_whatsapp=True, score=17, state=S.QUALIFYING,
                    channel_switched=True)
    for step in (1, 2):
        s.add(Event(lead_id=lead.id, channel="email", kind="sent", step=step, occurred_at=NOW,
                    received_at=NOW, processed=True, data={}))
    s.add(Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW, received_at=NOW,
                processed=True, verdict="human",
                data={"text": "Sounds great, what does it cost?", "classification": {"label": "question"}}))
    s.add(ChatMessage(lead_id=lead.id, direction="in", author="lead", text="Hi, can I book a demo?",
                      created_at=NOW))
    s.add(ChatMessage(lead_id=lead.id, direction="out", author="bot", text="Sure! Here is the link.",
                      created_at=NOW + timedelta(seconds=1)))
    s.commit()
    assert post(client, form("51-200", "within 3 months")).status_code == 200
    assert post(client, booking()).status_code == 200
    assert post(client, (FIX / "cal_meeting_ended.json").read_bytes()).status_code == 200
    lead = fresh(s, lead.id)
    assert lead.state == S.HANDED_OFF.value
    [h] = alerts(s, "handoff")
    for part in ("Asha Rao", "Acme", "CTO", "webinar", "17", "hot", "1, 2", "question",
                 "Sounds great, what does it cost?", "lead: Hi, can I book a demo?",
                 "bot: Sure! Here is the link.", "high", "51-200", "within 3 months",
                 "2026-11-20T09:00:00Z", "email", "whatsapp"):
        assert part in h.text, part
    assert ("send", "handoff", lead.id) in deps.alerter.calls


def test_handoff_summary_contents_and_cap(session):
    lead = make_lead(session, name="N" * 300, state=S.HANDED_OFF.value, score=3)
    for i in range(60):
        session.add(ChatMessage(lead_id=lead.id, direction="in", author="lead", text=f"m{i} " + "z" * 500,
                                created_at=NOW + timedelta(seconds=i)))
    for i in range(6):
        session.add(Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW + timedelta(seconds=i),
                          received_at=NOW, processed=True,
                          data={"text": f"reply{i}\nline2 " + "r" * 1000, "classification": {"label": "unsure"}}))
    session.flush()
    text = build_handoff_summary(session, lead)
    assert len(text) <= SUMMARY_MAX
    assert "cold" in text
    assert "m59" in text and "m39" not in text  # last 20 only
    assert "reply5" in text and "reply3" in text and "reply2" not in text
    assert "r" * 301 not in text and "z" * 201 not in text
    assert "reply5 line2" in text  # newlines in third-party text are flattened


def test_handoff_summary_minimal_lead(session):
    lead = make_lead(session)
    text = build_handoff_summary(session, lead)
    assert f"lead {lead.id}" in text and "none" in text.lower()


def test_make_handoff_sends_alert(session):
    alerter = FakeAlerter()
    lead = make_lead(session)
    make_handoff(alerter)(session, lead, NOW)
    assert alerter.calls == [("send", "handoff", lead.id)]
    assert session.scalars(select(AlertRow)).one().kind == "handoff"


def test_no_pii_in_logs(env, caplog):
    client, s, _ = env
    add_lead(s)
    caplog.set_level(logging.DEBUG)
    post(client, booking())
    post(client, booking(email="stranger@nowhere.com", uid="bk_x"))
    post(client, form("1-10", "exploring"))
    text = caplog.text.lower()
    for pii in ("lead@example.com", "stranger@nowhere.com", "nowhere.com", "1-10", "exploring"):
        assert pii not in text


# ------------------------------------------------------------------ rules (ruling 3)

def snap(**kw) -> LeadSnapshot:
    from types import MappingProxyType
    d = dict(id=1, state=S.QUALIFYING, score=0, step_sent=1, active_channel="email",
             channel_switched=False, has_usable_email=True, has_wa_optin=False, email="a@b.com",
             phone=None, wa_human_owner=None, wa_window_open=False, flags={})
    d.update(kw)
    d["flags"] = MappingProxyType(dict(d["flags"]))
    return LeadSnapshot(**d)


def cal_ev(kind, **data) -> EventSnapshot:
    from types import MappingProxyType
    return EventSnapshot(kind=kind, channel="cal", tag=None, verdict="n/a", step=None, occurred_at=NOW,
                         data=MappingProxyType(data))


@pytest.mark.parametrize("st", [S.IN_SEQUENCE, S.ENGAGED, S.QUALIFYING])
def test_rule_low_fit(st):
    assert decide(snap(state=st), cal_ev("form_submitted", fit="low"), NOW) == [
        SetFlag("fit", "low"), StopAllOutreach(), SetState(S.NURTURE, "low_fit"),
        SetNurtureUntil(NOW + timedelta(days=90))]


def test_rule_low_fit_already_nurture():
    assert decide(snap(state=S.NURTURE), cal_ev("form_submitted", fit="low"), NOW) == [
        SetFlag("fit", "low"), StopAllOutreach(), SetNurtureUntil(NOW + timedelta(days=90))]


@pytest.mark.parametrize("st", [S.BOOKED, S.PAUSED])
def test_rule_low_fit_other_states_flag_only(st):
    assert decide(snap(state=st), cal_ev("form_submitted", fit="low"), NOW) == [SetFlag("fit", "low")]


@pytest.mark.parametrize("fit", ["high", "medium"])
def test_rule_good_fit_flag(fit):
    assert decide(snap(), cal_ev("form_submitted", fit=fit), NOW) == [SetFlag("fit", fit)]


def test_rule_bad_fit_value_nothing():
    assert decide(snap(), cal_ev("form_submitted", fit="??"), NOW) == []


@pytest.mark.parametrize("st", sorted(TERMINAL, key=lambda s: s.value))
@pytest.mark.parametrize("kind", ["form_submitted", "booking_cancelled", "booking_rescheduled"])
def test_rule_terminal_nothing(st, kind):
    assert decide(snap(state=st), cal_ev(kind, fit="low", uid="u", end="e"), NOW) == []


def test_rule_cancel():
    out = decide(snap(state=S.BOOKED, flags={"booking_uid": "u"}), cal_ev("booking_cancelled", uid="u"), NOW)
    assert out == [SetState(S.QUALIFYING, "booking_cancelled"), Alert("booked")]
    assert out[1].text.endswith("booking cancelled")
    assert decide(snap(state=S.QUALIFYING), cal_ev("booking_cancelled", uid="u"), NOW) == []


def test_rule_reschedule():
    assert decide(snap(state=S.BOOKED), cal_ev("booking_rescheduled", uid="u2", end="E"), NOW) == [
        SetFlag("booking_end", "E"), SetFlag("booking_uid", "u2")]


def test_rule_booking_created_sets_booking_flags():
    out = decide(snap(state=S.QUALIFYING), cal_ev("booking_created", uid="u", end="E"), NOW)
    assert SetFlag("booking_end", "E") in out and SetFlag("booking_uid", "u") in out
    assert SetState(S.BOOKED, "booking_created") in out


@pytest.mark.parametrize("kind", ["no_show", "meeting_ended"])
def test_rule_stale_booking_uid_ignored(kind):
    lead = snap(state=S.BOOKED, flags={"booking_uid": "new"})
    assert decide(lead, cal_ev(kind, uid="old"), NOW) == []
    assert decide(lead, cal_ev(kind, uid="new"), NOW) != []
    assert decide(snap(state=S.BOOKED), cal_ev(kind, uid="any"), NOW) != []
