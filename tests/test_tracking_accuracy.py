"""Tracking accuracy + deliverability for the mailpit / gmail tools:
Microsoft Safe Links scanner ranges, labelled unsubscribe-page visits (never acted on),
List-Unsubscribe one-click headers, same-thread follow-ups, optional open pixel."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from outreach import worker
from outreach.app import create_app
from outreach.botfilter import Scanners, load_scanners
from outreach.db import init_db, make_engine
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.mailpit import MailpitEmailTool, thread_subject
from outreach.links import make_link, make_unsubscribe_url
from outreach.models import Event, Lead, Suppression
from outreach.outreach import Outreach
from outreach.sequencer import SCHEDULE_DAYS, enroll
from outreach.whatsapp.fake import FakeWhatsApp
from tests.conftest import NOW, make_lead
from tests.fakes import WEBHOOK_SECRET, TestSession, make_app_engine, make_deps, make_settings

SETTINGS = make_settings()
MS_SCANNER_IP = "74.224.61.24"  # Microsoft Defender Safe Links (seen 2026-11-17)
OFFICE_IP = "103.61.75.147"


# ---------------------------------------------------------------- worker: scanner vs person

@pytest.fixture
def env(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    outreach = Outreach(SETTINGS)
    deps = make_deps(tool=FakeEmailTool(webhook_secret=WEBHOOK_SECRET), wa=FakeWhatsApp(SETTINGS), outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(factory=factory, deps=deps)
    engine.dispose()


def _lead(env, **kw):
    with env.factory() as s:
        lead = make_lead(s, verify_status="valid", active_channel="email", step_sent=2,
                         last_sent_at=NOW - timedelta(minutes=4), **kw)
        s.commit()
        return lead.id


def _click(env, lead_id, tag, ip, at=NOW, step=2):
    with env.factory() as s:
        s.add(Event(lead_id=lead_id, channel="email", kind="click", tag=tag, step=step, occurred_at=at,
                    received_at=at, ip=ip, user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/120",
                    processed=False, data={}))
        s.commit()


def _tick(env, at):
    return worker.tick(env.factory, env.deps, lambda: at, SETTINGS, scanners=load_scanners())


def _events(env, lead_id):
    with env.factory() as s:
        return list(s.scalars(select(Event).where(Event.lead_id == lead_id, Event.kind == "click")))


def test_microsoft_scanner_booking_click_minutes_later_is_bot_and_scores_nothing(env):
    lid = _lead(env)
    _click(env, lid, "booking", MS_SCANNER_IP)  # 4 min after the send: passes the "too soon" check
    _tick(env, NOW + timedelta(minutes=1))
    (ev,) = _events(env, lid)
    assert ev.verdict == "bot" and ev.processed is True
    with env.factory() as s:
        assert s.get(Lead, lid).score == 0


@pytest.mark.parametrize("ip,verdict", [(MS_SCANNER_IP, "bot"), (OFFICE_IP, "human")])
def test_unsubscribe_page_visit_is_labelled_but_never_acted_on(env, ip, verdict):
    lid = _lead(env)
    _click(env, lid, "unsubscribe", ip)
    _tick(env, NOW + timedelta(minutes=1))
    (ev,) = _events(env, lid)
    assert ev.verdict == verdict and ev.processed is True
    with env.factory() as s:
        lead = s.get(Lead, lid)
        assert lead.state == "in_sequence" and lead.score == 0
        assert s.scalar(select(func.count()).select_from(Suppression)) == 0


def test_scanner_visiting_every_link_marks_all_bot(env):
    lid = _lead(env)
    _click(env, lid, "booking", "198.51.100.7")
    _click(env, lid, "unsubscribe", "198.51.100.7", at=NOW + timedelta(seconds=1))
    _tick(env, NOW + timedelta(minutes=1))
    assert sorted(e.verdict for e in _events(env, lid)) == ["bot", "bot"]


# ---------------------------------------------------------------- one-click unsubscribe URL

def test_list_unsubscribe_url_one_click_post_unsubscribes():
    engine = make_app_engine()
    app = create_app(SETTINGS, deps=make_deps(), engine=engine, clock=lambda: NOW,
                     scanners=Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=()))
    client = TestClient(app, raise_server_exceptions=False)
    s = TestSession(bind=engine)
    try:
        lead = make_lead(s)
        s.commit()
        url = make_unsubscribe_url(lead.id, 2, settings=SETTINGS)
        path = "/u/" + url.rsplit("/u/", 1)[1]
        assert path.split("/u/")[1] == make_link(lead.id, 2, "unsubscribe", settings=SETTINGS).rsplit("/r/", 1)[1]
        assert client.get(path).status_code == 200  # GET = confirm page only
        assert s.scalar(select(func.count()).select_from(Suppression)) == 0
        s.rollback()  # end the test's read transaction (one shared in-memory connection)
        r = client.post(path, content=b"List-Unsubscribe=One-Click",
                        headers={"content-type": "application/x-www-form-urlencoded"})
        assert r.status_code == 200
        assert s.scalar(select(func.count()).select_from(Suppression)) >= 1
    finally:
        s.close()
        engine.dispose()


# ---------------------------------------------------------------- sending: headers, thread, pixel

class SMTPStub:
    sent: list = []

    def __init__(self, *a, **kw):
        pass

    def send_message(self, msg):
        SMTPStub.sent.append(msg)

    def login(self, *a):
        pass

    def quit(self):
        pass


class RecordingTool:
    def __init__(self):
        self.variables = None

    def add_lead(self, campaign_id, lead, variables):
        self.variables = dict(variables)
        return "t-1"


def _enrolled_vars(settings):
    engine = make_app_engine()
    s = TestSession(bind=engine)
    try:
        lead = make_lead(s)
        s.commit()
        tool = RecordingTool()
        enroll(s, lead, tool, NOW, settings=settings)
        return lead.id, tool.variables
    finally:
        s.close()
        engine.dispose()


def test_enroll_sets_unsubscribe_url_and_pixel_per_step():
    lead_id, v = _enrolled_vars(SETTINGS)
    for step in SCHEDULE_DAYS:
        assert v[f"unsubscribe_url_{step}"] == make_unsubscribe_url(lead_id, step, settings=SETTINGS)
        assert f"open_pixel_{step}" in v


def test_open_pixel_can_be_turned_off():
    _, v = _enrolled_vars(make_settings(email_open_pixel=False))
    assert not any(k.startswith("open_pixel_") for k in v)
    assert all(f"unsubscribe_url_{step}" in v for step in SCHEDULE_DAYS)


def _tool(tmp_path, clock):
    SMTPStub.sent = []
    return MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)


def _vars():
    v = {}
    for i in SCHEDULE_DAYS:
        v.update({f"subject_{i}": f"Subject {i}", f"body_{i}": f"Body {i}",
                  f"unsubscribe_url_{i}": f"https://x.test/u/tok{i}"})
    return v


def test_every_send_and_reply_has_one_click_unsubscribe_headers(tmp_path):
    t = [NOW]
    tool = _tool(tmp_path, lambda: t[0])
    tid = tool.add_lead("c", SimpleNamespace(id=1, email="lead@example.com"), _vars())
    tool.send_due(SCHEDULE_DAYS)
    t[0] = NOW + timedelta(days=4)
    tool.send_due(SCHEDULE_DAYS)
    assert tool.reply(tid, "Here is the booking link")
    first, second, reply = SMTPStub.sent
    assert first["List-Unsubscribe"] == "<https://x.test/u/tok1>"
    assert second["List-Unsubscribe"] == "<https://x.test/u/tok2>"
    assert reply["List-Unsubscribe"] == "<https://x.test/u/tok2>"  # the last step sent
    for m in (first, second, reply):
        assert m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert second["Subject"] == reply["Subject"] == "Re: Subject 1"
    assert reply["In-Reply-To"] == second["Message-ID"]


def test_no_unsubscribe_url_no_header(tmp_path):
    tool = _tool(tmp_path, lambda: NOW)
    v = {k: val for i in SCHEDULE_DAYS for k, val in ((f"subject_{i}", "S"), (f"body_{i}", "B"))}
    tool.add_lead("c", SimpleNamespace(id=1, email="lead@example.com"), v)
    tool.send_due(SCHEDULE_DAYS)
    assert SMTPStub.sent[0]["List-Unsubscribe"] is None


@pytest.mark.parametrize("given,expected", [("Quick idea", "Re: Quick idea"), ("Re: Quick idea", "Re: Quick idea"),
                                            ("RE: re:  Quick idea", "Re: Quick idea")])
def test_thread_subject_never_doubles_re(given, expected):
    assert thread_subject(given) == expected
