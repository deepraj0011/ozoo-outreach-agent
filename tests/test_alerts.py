from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from sqlalchemy import select

from outreach.alerts import SlackAlerter, unposted_alert_ids
from outreach.models import Alert as AlertRow
from tests.conftest import NOW, make_lead

URL = "https://hooks.slack.com/services/x"


def alerter(**kw):
    return SlackAlerter(URL, sleep=lambda s: None, **kw)


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
    payload = json.loads(route.calls[0].request.content)
    assert payload == {"text": f"[hot_lead] lead #{lead.id} Asha Rao @ Acme: hot lead"}
    assert row(session).posted_at == NOW


@respx.mock
def test_slack_posted_only_after_commit(session):
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
def test_slack_not_posted_on_rollback(session):
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
    assert [json.loads(c.request.content)["text"].rsplit(": ", 1)[1] for c in route.calls] == ["kept"]


@respx.mock
def test_slack_failure_leaves_posted_at_null(session, caplog):
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
def test_slack_5xx_leaves_posted_at_null(session):
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
    assert json.loads(route.calls[0].request.content)["text"] == "[unmatched_reply] no lead: reply from unknown"


def test_integration_error_rows_unposted(session):
    from outreach.actions import record_integration_error

    record_integration_error(session, None, "x failed", NOW)
    session.commit()
    r = row(session)
    assert r.kind == "integration_error" and r.posted_at is None and r.created_at == NOW


@respx.mock
def test_slack_not_posted_on_savepoint_release(session):
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
