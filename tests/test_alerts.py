from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from sqlalchemy import select

from outreach.alerts import TeamsAlerter, unposted_alert_ids
from outreach.models import Alert as AlertRow
from tests.conftest import NOW, make_lead
from tests.fakes import TEAMS_WEBHOOK_URL

URL = TEAMS_WEBHOOK_URL


def card_texts(request) -> list:
    """The TextBlock texts of the posted Adaptive Card, after checking the envelope."""
    payload = json.loads(request.content)
    assert payload["type"] == "message"
    (att,) = payload["attachments"]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["contentUrl"] is None
    card = att["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    for block in card["body"]:
        assert block["type"] == "TextBlock" and block["wrap"] is True
    return [b["text"] for b in card["body"]]


def alerter(**kw):
    return TeamsAlerter(URL, sleep=lambda s: None, **kw)


def row(session):
    session.expire_all()
    return session.scalars(select(AlertRow)).one()


@respx.mock
def test_send_writes_row_and_posts_after_commit(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session, name="Asha Rao", company="Acme")
    alerter().send("hot_lead", "hot lead", lead, NOW, session=session)
    r = session.scalars(select(AlertRow)).one()
    assert (r.lead_id, r.kind, r.text, r.created_at) == (lead.id, "hot_lead", "hot lead", NOW)
    session.commit()
    assert card_texts(route.calls[0].request) == [f"Alert: hot_lead, lead {lead.id}", "Asha Rao @ Acme", "hot lead"]
    assert row(session).posted_at == NOW


@respx.mock
def test_teams_posted_only_after_commit(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    alerter().send("question", "q", lead, NOW)  # session taken from the lead
    session.flush()
    assert route.call_count == 0  # nothing leaves while the transaction is open
    session.commit()
    assert route.call_count == 1
    session.commit()  # a later commit does not re-post
    assert route.call_count == 1


@respx.mock
def test_teams_not_posted_on_rollback(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    session.commit()
    alerter().send("question", "q", lead, NOW, session=session)
    session.rollback()
    session.commit()
    assert route.call_count == 0
    assert session.scalars(select(AlertRow)).all() == []


@respx.mock
def test_savepoint_rollback_drops_only_its_alerts(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    a = alerter()
    a.send("question", "kept", lead, NOW, session=session)
    with pytest.raises(RuntimeError):
        with session.begin_nested():
            a.send("question", "dropped", lead, NOW, session=session)
            raise RuntimeError()
    session.commit()
    assert [card_texts(c.request)[-1] for c in route.calls] == ["kept"]


@respx.mock
def test_teams_failure_leaves_posted_at_null(session, caplog):
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    lead = make_lead(session, name="Secret Name", email="secret@acme.com")
    with caplog.at_level(logging.DEBUG):
        alerter().send("booked", "meeting booked", lead, NOW, session=session)
        session.commit()  # never raises
    r = row(session)
    assert r.kind == "booked" and r.posted_at is None
    assert unposted_alert_ids(session) == [r.id]
    assert "Secret Name" not in caplog.text and "secret@acme.com" not in caplog.text


@respx.mock
def test_teams_5xx_leaves_posted_at_null(session):
    respx.post(URL).mock(return_value=httpx.Response(500))
    alerter().send("unmatched_reply", "from x", None, NOW, session=session)
    session.commit()
    r = row(session)
    assert r.lead_id is None and r.posted_at is None


@respx.mock
def test_unmatched_lead_text(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    alerter().send("unmatched_reply", "reply from unknown", None, NOW, session=session)
    session.commit()
    assert card_texts(route.calls[0].request) == ["Alert: unmatched_reply, no lead", "reply from unknown"]


def test_integration_error_rows_unposted(session):
    from outreach.actions import record_integration_error

    record_integration_error(session, None, "x failed", NOW)
    session.commit()
    r = row(session)
    assert r.kind == "integration_error" and r.posted_at is None and r.created_at == NOW


@respx.mock
def test_teams_not_posted_on_savepoint_release(session):
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    with session.begin_nested():
        alerter().send("question", "q", lead, NOW, session=session)
    assert route.call_count == 0  # savepoint released, outer transaction still open
    session.commit()
    assert route.call_count == 1
    assert row(session).posted_at == NOW


@respx.mock
def test_executor_alert_posts_after_outer_commit(session):
    from outreach.actions import Alert, execute
    from tests.fakes import make_deps

    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    session.commit()
    execute(session, lead, [Alert("hot_lead", "hot")], make_deps(alerter=alerter()), NOW)
    assert route.call_count == 0  # external() savepoint released; nothing posted yet
    session.commit()
    assert route.call_count == 1
    assert row(session).posted_at == NOW


@respx.mock
def test_ingest_unmatched_alert_posts_after_commit_only_once(session):
    from outreach.botfilter import Scanners
    from outreach.emailtool.base import NormalizedEvent
    from outreach.ingest import ingest
    from tests.fakes import make_deps

    route = respx.post(URL).mock(return_value=httpx.Response(200))
    ev = NormalizedEvent(provider_event_id="u1", channel="email", kind="reply", occurred_at=NOW,
                         email="nobody@nowhere.io", data={"text": "hi"})
    ingest(session, [ev], make_deps(alerter=alerter()), NOW, Scanners((), (), ()))  # commits
    assert route.call_count == 1
    assert row(session).posted_at == NOW


@respx.mock
def test_executor_alert_not_posted_when_outer_rolls_back(session):
    from outreach.actions import Alert, execute
    from tests.fakes import make_deps

    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session)
    session.commit()
    execute(session, lead, [Alert("hot_lead", "hot")], make_deps(alerter=alerter()), NOW)
    session.rollback()
    session.commit()
    assert route.call_count == 0



# ---------------------------------------------------------------- Teams card escaping

EVIL = "[Open CRM](https://evil.example) **bold** <at>x</at> see https://evil.example/x"


def _posted(route) -> str:
    return "\n".join(card_texts(route.calls[-1].request))


def assert_inert(posted: str) -> None:
    low = posted.lower()
    assert "](" not in posted and "http" not in low and "<at>" not in low and "www." not in low
    assert "://" not in posted and "**" not in posted and "<" not in posted and ">" not in posted


@pytest.mark.parametrize("raw", [
    "[Open CRM](https://evil.example)", "**bold**", "<at>x</at>", "https://evil.example/x",
    "HTTPS://Evil.Example", "www.evil.example", "ht\u200btp://evil.example", "`code` _it_ # h",
    "www.3m.com", "www.a1b.com/x", "WWW.3m.com",
    "ｈｔｔｐｓ：／／evil.example", "evil。example", "evil\u2024example", "ｗｗｗ．evil．example",
])
def test_teams_escape_makes_text_inert(raw):
    from outreach.alerts import teams_escape

    out = teams_escape(raw)
    assert_inert(out)
    assert "evil.example" not in out.lower() or "[.]" in out
    assert teams_escape(raw) == out  # deterministic


def test_teams_escape_maps_and_strips():
    from outreach.alerts import teams_escape

    assert teams_escape("[a](b) *c* _d_ #e `f` <g>") == "［a］（b） ∗c∗ ＿d＿ ＃e ˋfˋ ‹g›"
    assert teams_escape("a\x00b\rc\nd\x1b\u202ee") == "abc\nde"
    assert teams_escape("https://evil.example/x") == "hxxps[:]//evil[.]example/x"
    assert teams_escape("www.evil.example") == "www[.]evil[.]example"
    assert teams_escape("plain text, 3.5 stars") == "plain text, 3.5 stars"


def test_teams_escape_www_nfkc_ipv4():
    from outreach.alerts import teams_escape

    assert teams_escape("www.3m.com") == "www[.]3m[.]com"
    assert teams_escape("www.a1b.com/x") == "www[.]a1b[.]com/x"
    assert teams_escape("ｈｔｔｐｓ：／／evil.example") == "hxxps[:]//evil[.]example"
    assert teams_escape("evil。example") == "evil[.]example"
    assert teams_escape("evil\u2024example") == "evil[.]example"
    assert teams_escape("1.2.3.4/x") == "1[.]2[.]3[.]4/x"
    assert teams_escape("see 10.0.0.255.") == "see 10[.]0[.]0[.]255."
    assert teams_escape("version 1.2.3 and 3.5 stars") == "version 1.2.3 and 3.5 stars"


def test_teams_escape_caps_length():
    from outreach.alerts import BLOCK_MAX, teams_escape

    out = teams_escape("x" * (BLOCK_MAX + 50))
    assert len(out) == BLOCK_MAX and out.endswith("…")


@respx.mock
def test_posted_card_escaped_row_stays_raw(session):
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    lead = make_lead(session, name="<at>Eve</at>", company="[Acme](https://evil.example)")
    alerter().send("question", EVIL, lead, NOW, session=session)
    session.commit()
    posted = _posted(route)
    assert_inert(posted)
    assert row(session).text == EVIL  # raw in the DB: a later re-post escapes exactly once
    assert row(session).posted_at == NOW  # Workflows answers 202


@respx.mock
def test_teams_url_never_logged(session, caplog):
    caplog.set_level(logging.DEBUG)
    respx.post(URL).mock(side_effect=httpx.ConnectError(f"cannot reach {URL}"))
    alerter().send("booked", "b", None, NOW, session=session)
    session.commit()
    respx.post(URL).mock(return_value=httpx.Response(500, text=URL))
    alerter().send("booked", "b", None, NOW, session=session)
    session.commit()
    respx.post(URL).mock(side_effect=RuntimeError(URL))
    alerter().send("booked", "b", None, NOW, session=session)
    session.commit()
    assert "sig=" not in caplog.text and "TEAMS-SIG-SECRET" not in caplog.text and "/workflows/" not in caplog.text
    assert "ConnectError" in caplog.text and "500" in caplog.text


@respx.mock
def test_teams_post_not_retried_on_500(session):
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    alerter().send("booked", "b", None, NOW, session=session)
    session.commit()
    assert route.call_count == 1  # idempotent=False: a 500 may have posted; never double-post


@respx.mock
def test_classifier_draft_answer_escaped_in_teams(session):
    from outreach.actions import Classify, execute
    from outreach.classifier import make_classifier
    from outreach.models import Event
    from tests.fakes import make_deps, make_settings
    from tests.test_classifier import FakeClient, out

    route = respx.post(URL).mock(return_value=httpx.Response(200))
    lead = make_lead(session, state="engaged", active_channel="email")
    ev = Event(lead_id=lead.id, channel="email", kind="reply", occurred_at=NOW, received_at=NOW,
               verdict="human", processed=False, data={"text": "how does it work?"})
    session.add(ev)
    session.flush()
    client = FakeClient(out("question", draft_answer="<at>all</at> see [here](https://evil.example)"))
    deps = make_deps(alerter=alerter(), classify=make_classifier(make_settings(), llm=client))
    execute(session, lead, [Classify()], deps, NOW, event=ev)
    session.commit()
    posted = _posted(route)
    assert_inert(posted)
    assert "‹at›all‹/at›" in posted
    assert "<at>all</at>" in row(session).text


@respx.mock
def test_unmatched_sender_alert_escaped_in_teams(session):
    from outreach.botfilter import Scanners
    from outreach.emailtool.base import NormalizedEvent
    from outreach.ingest import ingest
    from tests.fakes import make_deps

    route = respx.post(URL).mock(return_value=httpx.Response(200))
    ev = NormalizedEvent(provider_event_id="r-evil", channel="email", kind="reply", occurred_at=NOW,
                         email="[x](https://evil.example)@evil.example", data={"text": "hi"})
    ingest(session, [ev], make_deps(alerter=alerter()), NOW, Scanners(ua_substrings=(), cidrs=(),
                                                                       apple_proxy_cidrs=()))
    posted = _posted(route)
    assert_inert(posted)
    assert "[x](https://evil.example)@evil.example" in row(session).text


# ---------------------------------------------------------------- Task 12: worker re-post / digest

@respx.mock
def test_repost_posts_the_stored_row(session):
    route = respx.post(URL).mock(return_value=httpx.Response(202))
    lead = make_lead(session, name="Asha Rao", company="Acme")
    r = AlertRow(lead_id=lead.id, kind="hot_lead", text="hot [x](http://evil.example)", created_at=NOW)
    session.add(r)
    session.flush()
    assert alerter().repost(session, r, NOW) is True
    texts = card_texts(route.calls[0].request)
    assert texts[0] == f"Alert: hot_lead, lead {lead.id}" and texts[1] == "Asha Rao @ Acme"
    assert "http://" not in texts[2]
    assert r.posted_at is None  # the worker records posted_at


@respx.mock
def test_repost_failure_returns_false_without_leaking_url(session, caplog):
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom " + URL))
    r = AlertRow(lead_id=None, kind="mailbox_paused", text="x", created_at=NOW)
    session.add(r)
    session.flush()
    assert alerter().repost(session, r, NOW) is False
    assert "sig=" not in caplog.text


@respx.mock
def test_post_text(session):
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    assert alerter().post_text("Digest", ["a", "b"], NOW) is False
    route.mock(return_value=httpx.Response(200))
    assert alerter().post_text("Digest", ["a", "b"], NOW) is True
    assert card_texts(route.calls[-1].request) == ["Digest", "a", "b"]
