"""Click accuracy: the JavaScript-confirmed click page (CLICK_JS_CONFIRM) and the hidden honeypot
link (EMAIL_HONEYPOT). A scanner that fetches without running JavaScript, or that also follows the
invisible link, is labelled bot; a person's browser click is human."""
from __future__ import annotations

import ipaddress
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from outreach import devsim, worker
from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.db import init_db, make_engine
from outreach.emailtool.fake import FakeEmailTool
from outreach.emailtool.mailpit import MailpitEmailTool
from outreach.links import make_link, make_trap_url, parse_token
from outreach.models import Event, Lead
from outreach.outreach import Outreach
from outreach.sequencer import SCHEDULE_DAYS, enroll
from outreach.whatsapp.fake import FakeWhatsApp
from tests.conftest import NOW, make_lead
from tests.fakes import WEBHOOK_SECRET, TestSession, make_app_engine, make_deps, make_settings

SETTINGS = make_settings()
NO_SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"}


def path(url: str) -> str:
    return "/r/" + url.rsplit("/r/", 1)[1]


# ---------------------------------------------------------------- the click page + beacon

@pytest.fixture
def app_env():
    engine = make_app_engine()
    clock = [NOW]
    app = create_app(SETTINGS, deps=make_deps(), engine=engine, clock=lambda: clock[0], scanners=NO_SCANNERS)
    client = TestClient(app, follow_redirects=False)
    s = TestSession(bind=engine)
    yield SimpleNamespace(client=client, s=s, clock=clock)
    s.close()
    engine.dispose()


def _lead(s):
    lead = make_lead(s)
    s.commit()
    return lead


def _click(env, lead_id, tag="pricing", channel="email"):
    r = env.client.get(path(make_link(lead_id, 1, tag, channel, settings=SETTINGS)), headers=BROWSER)
    env.s.expire_all()
    ev = env.s.scalars(select(Event).where(Event.kind == "click").order_by(Event.id.desc())).first()
    snap = SimpleNamespace(id=ev.id, data=dict(ev.data or {}), processed=ev.processed)
    env.s.rollback()  # end the read transaction: the app shares the one in-memory connection
    return r, snap


def test_email_click_gets_confirm_page_not_redirect(app_env):
    lead = _lead(app_env.s)
    r, ev = _click(app_env, lead.id)
    assert r.status_code == 200 and "location" not in r.headers
    csp = r.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp and "default-src 'none'" in csp and r.headers["cache-control"] == "no-store"
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    assert f'<script nonce="{nonce}">' in r.text
    assert devsim.click_target(r.text).startswith(SETTINGS.link_targets["pricing"] + "?oz_v=")
    assert ev.data == {"js_check": True} and ev.processed is False
    assert devsim.click_confirm(r.text).path == f"{path(make_link(lead.id, 1, 'pricing', settings=SETTINGS))}/ok?e={ev.id}"


def test_beacon_marks_the_click_confirmed(app_env):
    lead = _lead(app_env.s)
    r, ev = _click(app_env, lead.id)
    assert app_env.client.post(devsim.click_confirm(r.text).path).status_code == 204
    app_env.s.expire_all()
    assert app_env.s.get(Event, ev.id).data == {"js_check": True, "js": True}


def test_beacon_for_another_link_or_event_is_ignored(app_env):
    lead = _lead(app_env.s)
    _, ev = _click(app_env, lead.id, tag="pricing")
    other = path(make_link(lead.id, 1, "booking", settings=SETTINGS))
    for p in (f"{other}/ok?e={ev.id}",  # token of another link
              f"{path(make_link(lead.id, 1, 'pricing', settings=SETTINGS))}/ok?e={ev.id + 99}",  # no such event
              f"/r/garbage/ok?e={ev.id}"):
        assert app_env.client.post(p).status_code == 204
    app_env.s.expire_all()
    assert "js" not in app_env.s.get(Event, ev.id).data


def test_late_beacon_is_ignored(app_env):
    lead = _lead(app_env.s)
    r, ev = _click(app_env, lead.id)
    app_env.clock[0] = NOW + timedelta(minutes=11)
    app_env.client.post(devsim.click_confirm(r.text).path)
    app_env.s.expire_all()
    assert "js" not in app_env.s.get(Event, ev.id).data


def test_whatsapp_click_is_still_a_plain_redirect(app_env):
    lead = _lead(app_env.s)
    r, ev = _click(app_env, lead.id, channel="whatsapp")
    assert r.status_code == 302 and ev.data == {}


def test_trap_link_is_stored_and_never_forwarded(app_env):
    lead = _lead(app_env.s)
    r = app_env.client.get(path(make_trap_url(lead.id, 1, settings=SETTINGS)), headers=BROWSER)
    assert r.status_code == 200 and "location" not in r.headers and "<script" not in r.text
    app_env.s.expire_all()
    ev = app_env.s.scalars(select(Event).where(Event.kind == "click")).one()
    assert ev.tag == "trap" and ev.processed is False


def test_click_page_target_cannot_break_out_of_the_script():
    from outreach.app import _click_page
    page = _click_page('https://x.test/a"</script><script>alert(1)</script>', "/r/t/ok?e=1", "n")
    assert page.count("<script") == 1 and "</script><script>" not in page


# ---------------------------------------------------------------- worker verdicts

@pytest.fixture
def w(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}", connect_args={"check_same_thread": False})
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    outreach = Outreach(SETTINGS)
    deps = make_deps(tool=FakeEmailTool(webhook_secret=WEBHOOK_SECRET), wa=FakeWhatsApp(SETTINGS), outreach=outreach)
    outreach.bind(deps)
    yield SimpleNamespace(factory=factory, deps=deps)
    engine.dispose()


def _wlead(w):
    with w.factory() as s:
        lead = make_lead(s, verify_status="valid", active_channel="email", step_sent=1,
                         last_sent_at=NOW - timedelta(hours=2))
        s.commit()
        return lead.id


def _wclick(w, lead_id, tag, ip, data, at=NOW):
    with w.factory() as s:
        s.add(Event(lead_id=lead_id, channel="email", kind="click", tag=tag, step=1, occurred_at=at,
                    received_at=at, ip=ip, user_agent=BROWSER["User-Agent"], processed=False, data=data))
        s.commit()


def _tick(w, at):
    worker.tick(w.factory, w.deps, lambda: at, SETTINGS, scanners=NO_SCANNERS)


def _verdicts(w, lead_id):
    with w.factory() as s:
        return {e.tag: (e.verdict, e.processed) for e in s.scalars(select(Event).where(
            Event.lead_id == lead_id, Event.kind == "click"))}


def test_confirmed_click_is_human_and_scores(w):
    lid = _wlead(w)
    _wclick(w, lid, "pricing", "203.0.113.9", {"js_check": True, "js": True})
    _tick(w, NOW + timedelta(minutes=1))
    assert _verdicts(w, lid)["pricing"] == ("human", True)
    with w.factory() as s:
        assert s.get(Lead, lid).score > 0


def test_click_page_without_beacon_is_bot_and_scores_nothing(w):
    lid = _wlead(w)
    _wclick(w, lid, "pricing", "203.0.113.9", {"js_check": True})
    _tick(w, NOW + timedelta(minutes=1))
    assert _verdicts(w, lid)["pricing"] == ("bot", True)
    with w.factory() as s:
        assert s.get(Lead, lid).score == 0


def test_click_waits_for_the_beacon_grace_period(w):
    lid = _wlead(w)
    _wclick(w, lid, "pricing", "203.0.113.9", {"js_check": True})
    fast = SETTINGS.model_copy(update={"time_scale": 1000.0})  # click_settle -> 0.03 s
    worker.tick(w.factory, w.deps, lambda: NOW + timedelta(seconds=3), fast, scanners=NO_SCANNERS)
    assert _verdicts(w, lid)["pricing"] == (None, False)  # not judged before the browser could answer


def test_trap_hit_is_bot_and_never_acted_on(w):
    lid = _wlead(w)
    _wclick(w, lid, "trap", "198.51.100.20", {})
    _tick(w, NOW + timedelta(minutes=1))
    assert _verdicts(w, lid)["trap"] == ("bot", True)
    with w.factory() as s:
        lead = s.get(Lead, lid)
        assert lead.score == 0 and lead.state == "in_sequence"


def test_click_from_the_trap_network_is_bot_even_minutes_later_and_with_js(w):
    lid = _wlead(w)
    _wclick(w, lid, "trap", "198.51.100.20", {})
    # a sandbox that runs JavaScript, from the same /24, 20 minutes later
    _wclick(w, lid, "booking", "198.51.100.77", {"js_check": True, "js": True}, at=NOW + timedelta(minutes=20))
    _tick(w, NOW + timedelta(minutes=21))
    assert _verdicts(w, lid)["booking"][0] == "bot"


def test_person_on_another_network_after_a_trap_hit_is_human(w):
    lid = _wlead(w)
    _wclick(w, lid, "trap", "198.51.100.20", {})
    _wclick(w, lid, "booking", "203.0.113.9", {"js_check": True, "js": True}, at=NOW + timedelta(minutes=20))
    _tick(w, NOW + timedelta(minutes=21))
    assert _verdicts(w, lid)["booking"][0] == "human"


# ---------------------------------------------------------------- the honeypot in the email

class RecordingTool:
    def add_lead(self, campaign_id, lead, variables):
        self.variables = dict(variables)
        return "t-1"


def _vars(settings):
    engine = make_app_engine()
    s = TestSession(bind=engine)
    try:
        lead = _lead(s)
        tool = RecordingTool()
        enroll(s, lead, tool, NOW, settings=settings)
        return lead.id, tool.variables
    finally:
        s.close()
        engine.dispose()


def test_every_step_gets_its_own_trap_link():
    lead_id, v = _vars(SETTINGS)
    for step in SCHEDULE_DAYS:
        assert parse_token(v[f"trap_url_{step}"].rsplit("/r/", 1)[1], settings=SETTINGS) == (lead_id, step, "trap", "email")


def test_honeypot_can_be_turned_off():
    _, v = _vars(make_settings(email_honeypot=False))
    assert not any(k.startswith("trap_url_") for k in v)


class SMTPStub:
    sent: list = []

    def __init__(self, *a, **kw):
        pass

    def send_message(self, msg):
        SMTPStub.sent.append(msg)

    def quit(self):
        pass


def test_sent_email_hides_the_trap_link_in_html_only(tmp_path):
    SMTPStub.sent = []
    tool = MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=lambda: NOW, smtp_factory=SMTPStub)
    v = {f"subject_{i}": "S" for i in SCHEDULE_DAYS}
    v.update({f"body_{i}": "Hello https://x.test/r/abc" for i in SCHEDULE_DAYS})
    v.update({f"trap_url_{i}": f"https://x.test/r/trap{i}" for i in SCHEDULE_DAYS})
    tool.add_lead("c", SimpleNamespace(id=1, email="lead@example.com"), v)
    tool.send_due(SCHEDULE_DAYS)
    (msg,) = SMTPStub.sent
    html = msg.get_body(("html",)).get_content()
    assert 'href="https://x.test/r/trap1" style="display:none"' in html
    assert "trap1" not in msg.get_body(("plain",)).get_content()  # never visible to a reader
    assert "<img" not in html  # no open pixel by default


# ---------------------------------------------------------------- opens settle like clicks (Outlook)

MS_SCANNERS = Scanners(ua_substrings=(), cidrs=(ipaddress.ip_network("74.224.0.0/14"),),
                       apple_proxy_cidrs=(ipaddress.ip_network("17.0.0.0/8"),))


def _wopen(w, lead_id, ip, ua=BROWSER["User-Agent"], at=NOW):
    with w.factory() as s:
        s.add(Event(lead_id=lead_id, channel="email", kind="open", step=1, occurred_at=at, received_at=at,
                    ip=ip, user_agent=ua, processed=False))
        s.commit()


def _open(w, lead_id):
    with w.factory() as s:
        ev = s.scalars(select(Event).where(Event.lead_id == lead_id, Event.kind == "open")).one()
        return ev.verdict, ev.processed, s.get(Lead, lead_id).variant_flags.get("human_opens")


def _mtick(w, at):
    worker.tick(w.factory, w.deps, lambda: at, SETTINGS, scanners=MS_SCANNERS)


def test_defender_open_from_unlisted_range_is_bot_via_trap(w):
    """Defender loads the pixel and clicks the hidden link from one data-centre network (and runs
    JavaScript): the open is a bot even though the range is not in scanners.yaml."""
    lid = _wlead(w)
    _wopen(w, lid, "4.213.147.0")
    _wclick(w, lid, "trap", "4.213.147.7", {})
    _mtick(w, NOW + timedelta(minutes=1))
    assert _open(w, lid) == ("bot", True, None)


def test_open_from_network_of_a_listed_scanner_click_is_bot(w):
    lid = _wlead(w)
    _wopen(w, lid, "74.224.61.30")  # listed range itself
    _wclick(w, lid, "unsubscribe", "74.224.61.24", {"js_check": True, "js": True})
    _mtick(w, NOW + timedelta(minutes=1))
    assert _open(w, lid)[0] == "bot"


def test_open_from_other_network_than_scanner_is_human_and_counted(w):
    lid = _wlead(w)
    _wclick(w, lid, "trap", "4.213.147.7", {})
    _wopen(w, lid, "103.61.75.147", at=NOW + timedelta(minutes=5))
    _mtick(w, NOW + timedelta(minutes=6))
    assert _open(w, lid) == ("human", True, 1)


def test_apple_proxy_open_is_machine(w):
    lid = _wlead(w)
    _wopen(w, lid, "17.58.0.1", ua="Mozilla/5.0")
    _mtick(w, NOW + timedelta(minutes=1))
    assert _open(w, lid) == ("machine", True, None)


def test_open_within_60s_of_send_is_bot(w):
    with w.factory() as s:
        lead = make_lead(s, verify_status="valid", active_channel="email", step_sent=1,
                         last_sent_at=NOW - timedelta(seconds=30))
        s.commit()
        lid = lead.id
    _wopen(w, lid, "198.51.100.2")
    _mtick(w, NOW + timedelta(minutes=1))
    assert _open(w, lid)[0] == "bot"


def test_open_waits_for_settle_window(w):
    lid = _wlead(w)
    _wopen(w, lid, "198.51.100.2")
    _mtick(w, NOW + timedelta(seconds=5))
    assert _open(w, lid) == (None, False, None)
