"""Task 19: real-visit tracking. An engaged visit (a person read the page) is the click signal a
security scanner cannot fake."""
from __future__ import annotations

import ipaddress
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from outreach import devsim
from outreach.botfilter import Scanners, classify_event
from outreach.links import (VISIT_PARAM, make_link, parse_lead_token, parse_open_token, parse_token,
                            parse_visit_token, visit_token, with_visit_token)
from outreach.models import Event
from outreach.rules import AddScore, Alert, SetFlag, SetVariant, decide
from outreach.states import LeadState as S
from outreach.visits import VISIT_JS, VisitStats, is_engaged, parse_beacon
from tests.conftest import NOW
from tests.fakes import make_settings
from tests.test_local_e2e import Local
from tests.test_rules import ev, lead_snap

SETTINGS = make_settings()


# ====================================================================== tokens

def test_visit_token_round_trip():
    tok = visit_token(7, 2, "pricing", "email", settings=SETTINGS)
    assert parse_visit_token(tok, settings=SETTINGS) == (7, 2, "pricing", "email")


def test_visit_token_rejects_tampering_and_other_token_kinds():
    tok = visit_token(7, 2, "pricing", settings=SETTINGS)
    payload, sig = tok.split(".")
    assert parse_visit_token(f"{payload}.{'0' * len(sig)}", settings=SETTINGS) is None
    click_tok = make_link(7, 2, "pricing", settings=SETTINGS).rsplit("/r/", 1)[1]
    assert parse_visit_token(click_tok, settings=SETTINGS) is None  # a click token is not a visit token
    assert parse_token(tok, settings=SETTINGS) is None  # ... and the other way round
    assert parse_lead_token(tok, settings=SETTINGS) is None and parse_open_token(tok, settings=SETTINGS) is None
    for junk in (None, "", "x" * 500, "abc", 12):
        assert parse_visit_token(junk, settings=SETTINGS) is None


@pytest.mark.parametrize("tag", ["booking", "unsubscribe", "trap", "nope"])
def test_visit_token_only_for_our_own_site_pages(tag):
    with pytest.raises(ValueError):
        visit_token(1, 1, tag, settings=SETTINGS)


def test_with_visit_token_keeps_other_parameters_and_replaces_an_old_token():
    url = with_visit_token("https://ozoo.ai/pricing?utm_source=mail&oz_v=old#plans", "NEW.abc")
    parts = urlsplit(url)
    assert parts.path == "/pricing" and parts.fragment == "plans"
    assert parse_qs(parts.query) == {"utm_source": ["mail"], VISIT_PARAM: ["NEW.abc"]}


# ====================================================================== beacon + engagement rule

@pytest.mark.parametrize("body", [
    b"", b"not json", b"[]", b'{"a": 30}', b'{"a": "30", "s": 50, "p": 1, "i": 3}',
    b'{"a": true, "s": 50, "p": 1, "i": 3}', b'{"a": -1, "s": 50, "p": 1, "i": 3}',
    b'{"a": 30, "s": 101, "p": 1, "i": 3}', b'{"a": 30, "s": 50, "p": 0, "i": 3}',
    b'{"a": 99999999, "s": 50, "p": 1, "i": 3}', b'{"a": NaN, "s": 50, "p": 1, "i": 3}',
    b'{"a": 30, "s": 50, "p": 1, "i": 3, "pad": "' + b"x" * 3000 + b'"}',
])
def test_parse_beacon_rejects_malformed_or_out_of_range(body):
    assert parse_beacon(body) is None


def test_parse_beacon_accepts_the_snippet_shape():
    assert parse_beacon(b'{"a": 31.6, "s": 64, "p": 2, "i": 12}') == VisitStats(31, 64, 2, 12)


@pytest.mark.parametrize("stats, engaged", [
    (VisitStats(15, 40, 1, 0), True),    # read and scrolled
    (VisitStats(20, 10, 1, 3), True),    # read and interacted (short page, no scroll)
    (VisitStats(9, 0, 2, 1), True),      # went on to a second page
    (VisitStats(14, 90, 1, 50), False),  # too short
    (VisitStats(60, 30, 1, 2), False),   # time but no sign of reading
    (VisitStats(7, 0, 3, 0), False),     # page hops without reading
    (VisitStats(0, 0, 1, 0), False),     # a fetch: what a scanner does
])
def test_is_engaged(stats, engaged):
    assert is_engaged(stats) is engaged


def test_snippet_thresholds_match_the_server_rule():
    assert "active >= 15 && (scroll >= 40 || inter >= 3)" in VISIT_JS
    assert "pages >= 2 && active >= 8" in VISIT_JS
    assert "now - lastInput < 30000" in VISIT_JS  # only active time counts
    assert "history.replaceState" in VISIT_JS      # the token is removed from the address bar


# ====================================================================== rules

def visit(tag: str, active_s: int = 40, verdict: object = "human"):
    return ev("visit", tag=tag, verdict=verdict, data={"active_s": active_s})


def alerts(out):
    return [a for a in out if isinstance(a, Alert)]


def test_engaged_pricing_visit_is_a_verified_hot_lead_even_while_clicks_need_a_check():
    lead = lead_snap(score=10, clicks_need_check=True, flags={"click_check_alerted": True})
    out = decide(lead, visit("pricing"), NOW)
    assert out[:2] == [AddScore(10), SetFlag("visits_scored", ["pricing"])]
    assert [a.kind for a in alerts(out)] == ["hot_lead"]
    assert "verified visit: read the pricing page (40s of active reading)" in alerts(out)[0].text
    assert SetFlag("hot_alerted", True) in out and SetFlag("verified_visit", True) in out


def test_visit_scores_once_per_page():
    lead = lead_snap(score=5, flags={"visits_scored": ["case_study"], "variants": ["case_study"]})
    assert decide(lead, visit("case_study"), NOW) == [SetFlag("verified_visit", True)]


def test_a_verified_visit_picks_the_next_emails_version():
    out = decide(lead_snap(score=0), visit("voice"), NOW)
    assert SetVariant("product") in out


def test_visit_crossing_hot_alerts_once():
    out = decide(lead_snap(score=12), visit("homepage"), NOW)
    assert [a.kind for a in alerts(out)] == ["hot_lead"]
    assert alerts(decide(lead_snap(score=20, flags={"hot_alerted": True}), visit("pricing"), NOW)) == []


def test_warm_visit_scores_without_alert():
    assert alerts(decide(lead_snap(), visit("case_study"), NOW)) == []


@pytest.mark.parametrize("verdict", ["bot", "machine", None])
def test_visit_that_is_not_human_does_nothing(verdict):
    assert decide(lead_snap(), visit("pricing", verdict=verdict), NOW) == []


def test_visit_on_closed_lead_does_nothing():
    assert decide(lead_snap(state=S.CLOSED), visit("pricing"), NOW) == []


# ====================================================================== bot filter

MS = Scanners(ua_substrings=(), cidrs=(ipaddress.ip_network("74.224.0.0/14"),), apple_proxy_cidrs=())


def _e(kind, ip, tag="pricing", at=NOW):
    return SimpleNamespace(kind=kind, channel="email", tag=tag, ip=ip, user_agent="Mozilla/5.0", occurred_at=at,
                           data=MappingProxyType({}))


def test_visit_from_a_scanner_network_is_bot():
    assert classify_event(_e("visit", "74.225.1.2"), NOW - timedelta(hours=1), [], MS) == "bot"


def test_visit_from_the_network_that_hit_the_trap_is_bot():
    trap = _e("click", "20.1.2.3", tag="trap", at=NOW - timedelta(minutes=2))
    assert classify_event(_e("visit", "20.1.2.99"), NOW - timedelta(hours=1), [trap], MS) == "bot"


def test_visit_from_a_person_is_human():
    trap = _e("click", "20.1.2.3", tag="trap", at=NOW - timedelta(minutes=2))
    assert classify_event(_e("visit", "49.36.10.20"), NOW - timedelta(hours=1), [trap], MS) == "human"


# ====================================================================== through the app

@pytest.fixture
def env(tmp_path):
    e = Local(tmp_path)
    yield e
    e.engine.dispose()


def _visits(env, lead_id):
    with env.factory() as s:
        return list(s.scalars(select(Event).where(Event.kind == "visit", Event.lead_id == lead_id)))


def test_snippet_is_served(env):
    r = env.client.get("/v.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert "sendBeacon" in r.text


def test_click_forwards_with_a_visit_token_for_the_same_lead(env):
    lid = env.started_email()
    r = env.send(devsim.click(env.settings, env.sim_lead(lid), tag="pricing"))
    token = parse_qs(urlsplit(devsim.click_target(r.text)).query)[VISIT_PARAM][0]
    assert parse_visit_token(token, settings=env.settings, now=env.clock()) == (lid, 1, "pricing", "email")


def test_booking_link_gets_no_visit_token(env):
    lid = env.started_email()
    r = env.send(devsim.click(env.settings, env.sim_lead(lid), tag="booking"))
    assert VISIT_PARAM not in (devsim.click_target(r.text) or "")


def test_click_then_real_reading_turns_a_check_into_a_verified_hot_lead(env):
    lid = env.started_email()
    r = env.send(devsim.click(env.settings, env.sim_lead(lid), tag="pricing"))
    env.send(devsim.click_confirm(r.text))
    env.advance(minutes=1)
    env.tick()
    assert len(env.alerts("click_check", lid)) == 1 and env.alerts("hot_lead", lid) == []
    assert env.send(devsim.visit(env.settings, env.sim_lead(lid), tag="pricing", active_s=45)).status_code == 204
    env.advance(minutes=1)
    env.tick()
    hot = env.alerts("hot_lead", lid)
    assert len(hot) == 1 and "verified visit" in hot[0].text
    assert env.lead(lid).score == 20  # 10 for the click, 10 for the visit
    assert [v.verdict for v in _visits(env, lid)] == ["human"]


def test_a_glance_is_not_stored(env):
    lid = env.started_email()
    r = env.send(devsim.visit(env.settings, env.sim_lead(lid), tag="pricing", active_s=4, scroll_pct=10))
    assert r.status_code == 204 and _visits(env, lid) == []


def test_bad_token_is_ignored_quietly(env):
    lid = env.started_email()
    req = devsim.visit(env.settings, env.sim_lead(lid), tag="pricing")
    req.path = req.path[:-3] + "000"
    assert env.send(req).status_code == 204 and _visits(env, lid) == []


def test_oversized_beacon_is_ignored(env):
    lid = env.started_email()
    req = devsim.visit(env.settings, env.sim_lead(lid), tag="pricing")
    req.body = b'{"a": 30, "s": 60, "p": 1, "i": 5, "x": "' + b"y" * 5000 + b'"}'
    assert env.send(req).status_code == 204 and _visits(env, lid) == []


def test_second_beacon_the_same_day_is_a_duplicate(env):
    lid = env.started_email()
    for _ in range(2):
        env.send(devsim.visit(env.settings, env.sim_lead(lid), tag="case_study"))
    env.advance(minutes=1)
    env.tick()
    assert len(_visits(env, lid)) == 1 and env.lead(lid).score == 5


def test_visit_tracking_off_ignores_beacons(tmp_path):
    e = Local(tmp_path)
    try:
        e.settings.visit_tracking = False
        lid = e.started_email()
        e.send(devsim.visit(e.settings, e.sim_lead(lid), tag="pricing"))
        assert _visits(e, lid) == []
        r = e.send(devsim.click(e.settings, e.sim_lead(lid), tag="pricing"))
        assert VISIT_PARAM not in devsim.click_target(r.text)
    finally:
        e.engine.dispose()


# ---------------------------------------------------------------- review fixes (task 19)

def test_visit_token_expires_after_two_days():
    from datetime import datetime, timedelta, timezone
    issued = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    tok = visit_token(3, 1, "pricing", settings=SETTINGS, issued_at=issued)
    assert parse_visit_token(tok, settings=SETTINGS, now=issued + timedelta(hours=47)) == (3, 1, "pricing", "email")
    assert parse_visit_token(tok, settings=SETTINGS, now=issued + timedelta(hours=49)) is None


def test_a_scanner_beacon_does_not_swallow_the_persons_beacon_the_same_day():
    from datetime import datetime, timezone
    from outreach.visits import VisitStats, visit_event
    st, now = VisitStats(30, 60, 1, 5), datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    scanner = visit_event(1, 1, "pricing", "email", st, now, "198.51.100.60", "x")
    person = visit_event(1, 1, "pricing", "email", st, now, "49.36.12.34", "x")
    again = visit_event(1, 1, "pricing", "email", st, now, "49.36.12.99", "x")
    assert scanner.provider_event_id != person.provider_event_id == again.provider_event_id


def test_snippet_counts_distinct_pages_and_ignores_mouse_jiggles():
    from outreach.visits import VISIT_JS
    assert '_last") !== location.pathname' in VISIT_JS
    assert 'e.type !== "mousemove"' in VISIT_JS and "if (ok) ss.setItem" in VISIT_JS
