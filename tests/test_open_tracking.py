"""Our own open tracking: signed 1x1 pixel in mailpit / gmail emails -> /o/<token>.gif -> open event."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from outreach.app import create_app
from outreach.botfilter import Scanners
from outreach.emailtool.mailpit import MailpitEmailTool, html_with_pixel
from outreach.links import (lead_token, make_link, make_open_pixel, parse_lead_token, parse_open_token,
                            parse_token)
from outreach.models import Event, Lead
from outreach.sequencer import SCHEDULE_DAYS, enroll
from tests.conftest import NOW, make_lead
from tests.fakes import TestSession, make_app_engine, make_deps, make_settings

SETTINGS = make_settings()
SCANNERS = Scanners(ua_substrings=("BadScanner",), cidrs=(), apple_proxy_cidrs=())
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"}


def tok(lead_id, step):
    return make_open_pixel(lead_id, step, settings=SETTINGS).rsplit("/o/", 1)[1]


# ---------------------------------------------------------------- tokens

def test_pixel_url_round_trip():
    url = make_open_pixel(7, 2, settings=SETTINGS)
    assert url.startswith(SETTINGS.link_base_url.rstrip("/") + "/o/") and url.endswith(".gif")
    t = url.rsplit("/o/", 1)[1]
    assert parse_open_token(t, settings=SETTINGS) == (7, 2)
    assert parse_open_token(t[:-4], settings=SETTINGS) == (7, 2)  # without .gif


@pytest.mark.parametrize("bad", ["", "x", "abc.def", "a" * 500, None, 5])
def test_bad_open_tokens(bad):
    assert parse_open_token(bad, settings=SETTINGS) is None


def test_tampered_and_wrong_secret():
    t = tok(7, 2)
    assert parse_open_token("A" + t[1:], settings=SETTINGS) is None
    assert parse_open_token(t, settings=make_settings(link_secret="another-secret-value-1234567890-abcdef")) is None


def test_tokens_never_cross_decode():
    click = make_link(7, 2, "pricing", settings=SETTINGS).rsplit("/r/", 1)[1]
    lead = lead_token(7, settings=SETTINGS)
    open_t = tok(7, 2)
    assert parse_open_token(click, settings=SETTINGS) is None
    assert parse_open_token(lead, settings=SETTINGS) is None
    assert parse_token(open_t[:-4], settings=SETTINGS) is None
    assert parse_lead_token(open_t[:-4], settings=SETTINGS) is None


# ---------------------------------------------------------------- endpoint

@pytest.fixture
def env():
    engine = make_app_engine()
    deps = make_deps()
    app = create_app(SETTINGS, deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False)
    s = TestSession(bind=engine)
    yield client, s
    s.close()
    engine.dispose()


def _lead(s):
    lead = make_lead(s)
    s.commit()
    return lead


def test_open_is_logged_and_image_served(env):
    client, s = env
    lead = _lead(s)
    r = client.get(f"/o/{tok(lead.id, 1)}", headers=BROWSER)
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
    assert r.content.startswith(b"GIF89a") and "no-store" in r.headers["cache-control"]
    ev = s.scalars(select(Event).where(Event.kind == "open")).one()
    assert (ev.lead_id, ev.step, ev.channel, ev.verdict, ev.processed) == (lead.id, 1, "email", "human", True)
    s.expire_all()
    assert s.get(Lead, lead.id).variant_flags.get("human_opens") == 1


def test_open_never_changes_state_or_score(env):
    client, s = env
    lead = _lead(s)
    before = (lead.state, lead.score)
    client.get(f"/o/{tok(lead.id, 1)}", headers=BROWSER)
    s.expire_all()
    fresh = s.get(Lead, lead.id)
    assert (fresh.state, fresh.score) == before


def test_reloads_within_five_minutes_count_once(env):
    client, s = env
    lead = _lead(s)
    for _ in range(3):
        assert client.get(f"/o/{tok(lead.id, 1)}", headers=BROWSER).status_code == 200
    assert len(s.scalars(select(Event).where(Event.kind == "open")).all()) == 1


def test_scanner_open_is_logged_as_bot_not_counted(env):
    client, s = env
    lead = _lead(s)
    client.get(f"/o/{tok(lead.id, 1)}", headers={"User-Agent": "BadScanner/1.0"})
    ev = s.scalars(select(Event).where(Event.kind == "open")).one()
    assert ev.verdict == "bot"
    s.expire_all()
    assert not s.get(Lead, lead.id).variant_flags.get("human_opens")


@pytest.mark.parametrize("path", ["/o/garbage.gif", "/o/x", "/o/" + "A" * 300])
def test_bad_token_still_gets_image_and_stores_nothing(env, path):
    client, s = env
    r = client.get(path, headers=BROWSER)
    assert r.status_code == 200 and r.content.startswith(b"GIF89a")
    assert s.scalars(select(Event)).all() == []


def test_unknown_lead_still_gets_image(env):
    client, s = env
    r = client.get(f"/o/{tok(999, 1)}", headers=BROWSER)
    assert r.status_code == 200 and r.content.startswith(b"GIF89a")


def test_open_logs_have_no_pii(env, caplog):
    client, s = env
    lead = _lead(s)
    caplog.set_level("INFO")
    client.get(f"/o/{tok(lead.id, 1)}", headers=BROWSER)
    assert lead.email not in caplog.text and "Mozilla" not in caplog.text


# ---------------------------------------------------------------- sequencer + email body

class RecordingTool:
    def __init__(self):
        self.variables = None

    def add_lead(self, campaign_id, lead, variables):
        self.variables = dict(variables)
        return "t-1"


def test_enroll_adds_a_pixel_per_step():
    engine = make_app_engine()
    s = TestSession(bind=engine)
    try:
        lead = _lead(s)
        tool = RecordingTool()
        enroll(s, lead, tool, NOW, settings=SETTINGS)
        for step in SCHEDULE_DAYS:
            assert parse_open_token(tool.variables[f"open_pixel_{step}"].rsplit("/o/", 1)[1],
                                    settings=SETTINGS) == (lead.id, step)
    finally:
        s.close()
        engine.dispose()


def test_html_body_escapes_links_and_has_pixel():
    body = "Hi <Bob> & co,\nsee https://x.test/r/abc.\nThanks"
    out = html_with_pixel(body, "https://x.test/o/p.gif")
    assert "&lt;Bob&gt; &amp; co" in out and "<br>" in out
    assert '<a href="https://x.test/r/abc">https://x.test/r/abc</a>.' in out
    assert '<img src="https://x.test/o/p.gif" width="1" height="1"' in out


class SMTPStub:
    sent: list = []

    def __init__(self, host, port, **kw):
        pass

    def send_message(self, msg):
        SMTPStub.sent.append(msg)

    def login(self, *a):
        pass

    def quit(self):
        pass


def _send_one(tool_factory, variables):
    SMTPStub.sent = []
    tool = tool_factory()
    tool.add_lead("c1", SimpleNamespace(id=1, email="lead@example.com"), variables)
    tool.send_due(SCHEDULE_DAYS)
    return SMTPStub.sent[0]


def test_mailpit_email_has_plain_and_html_with_pixel(tmp_path):
    v = {f"subject_{i}": f"S{i}" for i in SCHEDULE_DAYS}
    v.update({f"body_{i}": f"Body {i} https://x.test/r/abc" for i in SCHEDULE_DAYS})
    v.update({f"open_pixel_{i}": f"https://x.test/o/p{i}.gif" for i in SCHEDULE_DAYS})
    msg = _send_one(lambda: MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=lambda: NOW,
                                             smtp_factory=SMTPStub), v)
    assert msg.get_body(("plain",)).get_content().strip() == "Body 1 https://x.test/r/abc"
    assert "https://x.test/o/p1.gif" in msg.get_body(("html",)).get_content()


def test_without_pixel_email_stays_plain_text(tmp_path):
    v = {f"subject_{i}": f"S{i}" for i in SCHEDULE_DAYS}
    v.update({f"body_{i}": f"Body {i}" for i in SCHEDULE_DAYS})
    msg = _send_one(lambda: MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=lambda: NOW,
                                             smtp_factory=SMTPStub), v)
    assert not msg.is_multipart() and msg.get_content_type() == "text/plain"


def test_gmail_email_has_pixel(tmp_path):
    from outreach.emailtool.gmail import GmailEmailTool
    import inspect
    params = inspect.signature(GmailEmailTool.__init__).parameters
    kw = {"address": "me@gmail.com", "app_password": "abcd efgh ijkl mnop", "state_file": tmp_path / "g.db",
          "now": lambda: NOW}
    kw["smtp_factory" if "smtp_factory" in params else "smtp"] = SMTPStub
    v = {f"subject_{i}": f"S{i}" for i in SCHEDULE_DAYS}
    v.update({f"body_{i}": f"Body {i}" for i in SCHEDULE_DAYS})
    v.update({f"open_pixel_{i}": f"https://x.test/o/p{i}.gif" for i in SCHEDULE_DAYS})
    msg = _send_one(lambda: GmailEmailTool("secret", **kw), v)
    assert "https://x.test/o/p1.gif" in msg.get_body(("html",)).get_content()
