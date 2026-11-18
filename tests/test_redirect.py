from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from outreach.app import create_app
from outreach.botfilter import Scanners
from tests.fakes import TestSession
from outreach.links import make_link
from outreach.models import Event, Lead, Suppression
from tests.conftest import NOW, make_lead
from tests.fakes import LINK_TARGETS, make_app_engine, make_deps, make_settings

SCANNERS = Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=())
SETTINGS = make_settings(click_js_confirm=False)  # the plain 302 path; the click page: test_click_confirm.py


@pytest.fixture
def env():
    engine = make_app_engine()
    deps = make_deps()
    app = create_app(SETTINGS, deps=deps, engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    s = TestSession(bind=engine)
    yield client, s, deps, engine
    s.close()
    engine.dispose()


def token(lead_id, step, tag, channel="email"):
    return make_link(lead_id, step, tag, channel, settings=SETTINGS).rsplit("/r/", 1)[1]


def lead_in(s, **kw):
    lead = make_lead(s, **kw)
    s.commit()
    return lead


def test_redirect_logs_and_302(env):
    client, s, deps, _ = env
    lead = lead_in(s)
    r = client.get(f"/r/{token(lead.id, 2, 'pricing')}",
                   headers={"User-Agent": "Mozilla/5.0 Test", "X-Forwarded-For": "198.51.100.9, 10.0.0.1"})
    assert r.status_code == 302
    assert r.headers["location"].startswith(LINK_TARGETS["pricing"] + "?oz_v=")  # visit token (task 19)
    assert r.headers["cache-control"] == "no-store"
    ev = s.scalars(select(Event)).one()
    assert (ev.lead_id, ev.kind, ev.tag, ev.step, ev.channel) == (lead.id, "click", "pricing", 2, "email")
    assert ev.ip == "testclient"  # final review B4: X-Forwarded-For ignored unless trusted
    assert ev.user_agent == "Mozilla/5.0 Test"
    assert ev.processed is False and ev.occurred_at == NOW
    assert deps.outreach.calls == [] and deps.tool.calls == []  # deferred to worker


def test_redirect_ua_truncated_and_client_ip(env):
    client, s, _, _ = env
    lead = lead_in(s)
    client.get(f"/r/{token(lead.id, 1, 'homepage', 'whatsapp')}", headers={"User-Agent": "A" * 2000})
    ev = s.scalars(select(Event)).one()
    assert len(ev.user_agent) == 512 and ev.channel == "whatsapp"
    assert ev.ip == "testclient"


def test_bad_token_goes_home(env):
    client, s, _, _ = env
    for bad in ["garbage", "a.b", token(1, 1, "pricing")[:-1] + "0"]:
        r = client.get(f"/r/{bad}")
        assert r.status_code == 302 and r.headers["location"] == LINK_TARGETS["homepage"]
    assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_missing_target_goes_home(env):
    client, s, _, engine = env
    lead = lead_in(s)
    app = create_app(make_settings(link_targets={"homepage": "https://ozoo.ai/", "booking": "https://cal.com/x"}, click_js_confirm=False), deps=make_deps(),
                     engine=engine, clock=lambda: NOW, scanners=SCANNERS)
    # token signed with same secret
    r = TestClient(app, follow_redirects=False).get(f"/r/{token(lead.id, 1, 'case_study')}")
    assert r.status_code == 302 and r.headers["location"] == "https://ozoo.ai/"


def test_redirect_db_failure_still_redirects(env):
    client, s, _, engine = env
    lead = lead_in(s)
    s.close()
    with engine.begin() as c:
        c.exec_driver_sql("DROP TABLE event")
    r = client.get(f"/r/{token(lead.id, 1, 'booking')}")
    # booking links carry the signed lead token for Cal.com (Task 11)
    assert r.status_code == 302 and r.headers["location"].startswith(LINK_TARGETS["booking"] + "?metadata%5Blead%5D=")


def test_redirect_unknown_lead_still_stored(env):
    client, s, _, _ = env
    r = client.get(f"/r/{token(424242, 1, 'product')}")
    assert r.status_code == 302
    assert s.scalars(select(Event)).one().lead_id is None


# ------------------------------------------------------------------ unsubscribe (ruling 9)


def both_channel_lead(s):
    return lead_in(s, email="u@acme.com", phone="+919812345678", consent_whatsapp=True, tool_lead_id="T-1")


def test_unsubscribe_link_goes_to_confirm_page(env):
    client, s, deps, _ = env
    lead = both_channel_lead(s)
    tok = token(lead.id, 2, "unsubscribe")
    r = client.get(f"/r/{tok}")
    assert r.status_code == 302 and r.headers["location"] == f"/u/{tok}"
    ev = s.scalars(select(Event)).one()
    # waits for the worker's human/bot label; the worker never acts on it (test_tracking_accuracy)
    assert ev.tag == "unsubscribe" and ev.processed is False
    assert s.scalar(select(func.count()).select_from(Suppression)) == 0


def test_unsubscribe_get_does_not_suppress(env):
    client, s, deps, _ = env
    lead = both_channel_lead(s)
    tok = token(lead.id, 2, "unsubscribe")
    r = client.get(f"/u/{tok}")
    assert r.status_code == 200
    assert "Unsubscribe from Ozoo.ai messages?" in r.text
    assert '<form method="post"' in r.text and "<script" not in r.text
    assert tok not in r.text and "u@acme.com" not in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-robots-tag"] == "noindex"
    s.expire_all()
    assert s.get(Lead, lead.id).state == "in_sequence"
    assert s.scalar(select(func.count()).select_from(Suppression)) == 0
    assert deps.tool.calls == []


def test_unsubscribe_get_invalid_token_404(env):
    client, _, _, _ = env
    assert client.get("/u/garbage").status_code == 404
    assert client.get(f"/u/{token(1, 1, 'pricing')}").status_code == 404  # wrong tag
    assert client.post("/u/garbage").status_code == 404


def test_unsubscribe_post_suppresses_both_channels(env):
    client, s, deps, _ = env
    lead = both_channel_lead(s)
    r = client.post(f"/u/{token(lead.id, 2, 'unsubscribe')}")
    assert r.status_code == 200 and "unsubscribed" in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["x-robots-tag"] == "noindex"
    s.expire_all()
    assert s.get(Lead, lead.id).state == "suppressed"
    rows = {(x.value, x.kind) for x in s.scalars(select(Suppression))}
    assert rows == {("u@acme.com", "email"), ("+919812345678", "phone")}
    ev = s.scalars(select(Event).where(Event.kind == "unsubscribe")).one()
    assert ev.provider_event_id == f"unsub:{lead.id}:2" and ev.lead_id == lead.id and ev.processed
    assert deps.tool.calls == [("block", "u@acme.com"), ("stop", "T-1")]  # final review A1: block list


def test_unsubscribe_post_twice_ok(env):
    client, s, deps, _ = env
    lead = both_channel_lead(s)
    url = f"/u/{token(lead.id, 2, 'unsubscribe')}"
    assert client.post(url).status_code == 200
    r = client.post(url)
    assert r.status_code == 200 and "unsubscribed" in r.text
    assert s.scalar(select(func.count()).select_from(Event).where(Event.kind == "unsubscribe")) == 1
    assert deps.tool.calls == [("block", "u@acme.com"), ("stop", "T-1")]  # final review A1: block list


def test_unsubscribe_one_click_rfc8058(env):
    client, s, _, _ = env
    lead = both_channel_lead(s)
    r = client.post(f"/u/{token(lead.id, 1, 'unsubscribe', 'whatsapp')}",
                    content=b"List-Unsubscribe=One-Click",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    s.expire_all()
    assert s.get(Lead, lead.id).state == "suppressed"
    assert s.scalars(select(Event).where(Event.kind == "unsubscribe")).one().channel == "whatsapp"


def test_healthz(env):
    client, _, _, _ = env
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
